"""TCP execution and tracing adapter for the existing semi-naive owner baseline."""

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import socket
import struct
import time


HERE = Path(__file__).resolve().parent
INPUT_DIR = HERE.parent / "precision_partition_20261017/measurements/em3d_final"
CASES = (
    "reference_k1",
    "hash_k2_s1_r0", "hash_k2_s2_r0",
    "method_k2_s1_r0", "method_k2_s2_r0",
    "metis_cut_k2_s1_r0", "metis_cut_k2_s2_r0",
    "hash_k4_s1_r0", "metis_cut_k4_s1_r0",
)


def read_input(path):
    numbers = iter(map(int, path.read_text().split()))
    nv, na, nf, nm = (next(numbers) for _ in range(4))

    def row(length):
        return tuple(next(numbers) for _ in range(length))

    masks = tuple(frozenset(row(next(numbers))) for _ in range(nm))
    node_masks = row(nv)
    field_masks = row(nf)
    copies = [row(2) for _ in range(next(numbers))]
    stores = [row(3) for _ in range(next(numbers))]
    loads = [row(3) for _ in range(next(numbers))]
    if next(numbers, None) is not None:
        raise ValueError("Extra integers after the recorded input")
    return nv, na, nf, masks, node_masks, field_masks, copies, stores, loads


def send(sock, value):
    data = json.dumps(value, separators=(",", ":")).encode("ascii")
    sock.sendall(struct.pack("!I", len(data)) + data)
    return len(data) + 4


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size - len(data))
        if not block:
            raise EOFError("worker connection closed")
        data.extend(block)
    return data


def receive(sock):
    size = struct.unpack("!I", read_exact(sock, 4))[0]
    return json.loads(read_exact(sock, size)), size + 4


class Worker:
    def __init__(self, worker_id, spec, owners, mode):
        self.id, self.owners, self.mode = worker_id, owners, mode
        (self.nv, self.na, self.nf, self.masks, self.node_masks,
         self.field_masks, copies, stores, loads) = spec
        self.initial = self.nv + self.na
        self.values = defaultdict(set)
        self.edges = defaultdict(dict)
        self.rules = defaultdict(list)
        for src, dst in copies:
            if self.owner(src) == self.id:
                self.edges[src][dst] = ["copy", src, dst]
        for index, (src, base, field) in enumerate(stores):
            if self.owner(base) == self.id:
                self.rules[base].append(("store", index, field, src))
        for index, (base, field, dst) in enumerate(loads):
            if self.owner(base) == self.id:
                self.rules[base].append(("load", index, field, dst))

    def owner(self, node):
        anchor = node if node < self.initial else self.nv + (node - self.initial) // self.nf
        return self.owners[anchor]

    def allowed(self, node, allocation):
        if node < self.nv:
            mask = self.node_masks[node]
        elif node < self.initial:
            return True
        else:
            mask = self.field_masks[(node - self.initial) % self.nf]
        return mask < 0 or allocation in self.masks[mask]

    def checkpoint(self):
        facts = []
        for node, values in sorted(self.values.items()):
            if self.nv <= node < self.initial:
                continue  # Internal constant sources are not reference tuples.
            for allocation in sorted(values):
                if node < self.nv:
                    facts.append(["V", node, allocation])
                else:
                    base, field = divmod(node - self.initial, self.nf)
                    facts.append(["H", base, field, allocation])
        return facts

    def step(self, inbox):
        started = time.process_time_ns()
        queue, outgoing, records = deque(inbox), [], []
        counters = Counter()

        def emit(kind, node, other, cause):
            destination = self.owner(node)
            event = [kind, self.id, destination, node, other, cause]
            if destination == self.id and self.mode == "drain":
                queue.append(event)
            else:
                outgoing.append(event)

        while queue:
            event = queue.popleft()
            kind, _, destination, node, other, cause = event
            if destination != self.id or self.owner(node) != self.id:
                raise ValueError("event delivered to wrong owner")
            if kind == "E":
                counters["edge_requests"] += 1
                edges = self.edges[node]
                if other in edges:
                    counters["duplicate_edges"] += 1
                    records.append([event, "duplicate_edge", 0])
                    continue
                edges[other] = cause
                counters["installed_edges"] += 1
                existing = sorted(self.values[node])
                counters["replayed_facts"] += len(existing)
                records.append([event, "installed", len(existing)])
                for allocation in existing:
                    emit("F", other, allocation, [*cause, "replay"])
                continue

            if kind != "F":
                raise ValueError("unknown event kind")
            counters["seed_inputs" if cause[0] == "seed" else "fact_candidates"] += 1
            values = self.values[node]
            if not self.allowed(node, other):
                counters["type_rejected"] += 1
                records.append([event, "type_rejected", 0])
                continue
            if other in values:
                counters["duplicate_facts"] += 1
                records.append([event, "duplicate_fact", 0])
                continue
            values.add(other)
            counters["accepted_facts"] += 1
            records.append([event, "accepted", 0])
            for dst, edge_cause in self.edges[node].items():
                emit("F", dst, other, [*edge_cause, "delta"])
            for operation, index, field, counterpart in self.rules.get(node, ()):
                counters["base_activations"] += 1
                heap = self.initial + other * self.nf + field
                src, dst = (heap, counterpart) if operation == "load" else (counterpart, heap)
                emit("E", src, dst, [operation, index, other])

        return dict(
            outgoing=outgoing, counters=dict(counters), records=records,
            compute_cpu_ns=time.process_time_ns() - started,
            checkpoint=self.checkpoint(),
        )


def worker_main(endpoint, worker_id, spec, owners, mode):
    worker = Worker(worker_id, spec, owners, mode)
    with socket.create_connection(endpoint, timeout=30) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send(sock, [worker_id, os.getpid()])
        while True:
            inbox, _ = receive(sock)
            if inbox is None:
                return
            send(sock, worker.step(inbox))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(case, mode, spec):
    partition_path = INPUT_DIR / f"{case}.partition"
    owners = list(map(int, partition_path.read_text().split()))
    nv, na = spec[:2]
    count = max(owners) + 1
    if len(owners) != nv + na or min(owners) != 0:
        raise ValueError("unexpected partition")
    ctx = mp.get_context("spawn")
    inbox = [[] for _ in range(count)]
    for allocation in range(na):
        node = nv + allocation
        worker = owners[node]
        inbox[worker].append(["F", worker, worker, node, allocation, ["seed", allocation]])

    rounds, connections, children, pids = [], {}, [], {}
    traffic = Counter()
    started = time.perf_counter_ns()
    with socket.socket() as listener:
        listener.settimeout(30)
        listener.bind(("127.0.0.1", 0))
        listener.listen(count)
        try:
            for worker in range(count):
                child = ctx.Process(
                    target=worker_main, args=(listener.getsockname(), worker, spec, owners, mode),
                )
                child.start()
                children.append(child)
            for _ in range(count):
                connection, _ = listener.accept()
                connection.settimeout(30)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                (worker, pid), nbytes = receive(connection)
                if worker in connections or pid != children[worker].pid:
                    raise ValueError("unexpected worker identity")
                connections[worker], pids[worker] = connection, pid
                traffic["worker_to_coordinator"] += nbytes

            ready = time.perf_counter_ns()
            while any(inbox):
                if len(rounds) >= 1000:
                    raise RuntimeError("round bound exceeded")
                for worker in range(count):
                    traffic["coordinator_to_worker"] += send(connections[worker], inbox[worker])
                inbox = [[] for _ in range(count)]
                outgoing, checkpoint, details = [], [], []
                for worker in range(count):
                    result, nbytes = receive(connections[worker])
                    traffic["worker_to_coordinator"] += nbytes
                    outgoing.extend(result.pop("outgoing"))
                    checkpoint.extend(result.pop("checkpoint"))
                    details.append(dict(worker=worker, **result))
                for event in outgoing:
                    inbox[event[2]].append(event)
                rounds.append(dict(
                    round=len(rounds) + 1, checkpoint=sorted(checkpoint),
                    outgoing=outgoing, workers=details,
                ))
            ended = time.perf_counter_ns()
            for connection in connections.values():
                traffic["coordinator_to_worker"] += send(connection, None)
            for child in children:
                child.join(10)
                if child.exitcode != 0:
                    raise RuntimeError(f"worker {child.pid} exit {child.exitcode}")
        finally:
            for connection in connections.values():
                connection.close()
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join()

    return dict(
        case=case, mode=mode, owners=owners, worker_pids=pids, transport="IPv4 TCP loopback",
        partition_sha256=digest(partition_path), input_sha256=digest(INPUT_DIR / "input.txt"),
        diagnostic_framed_tcp_bytes=dict(traffic), setup_ns=ready - started,
        traced_solve_wall_ns=ended - ready, final=rounds[-1]["checkpoint"], rounds=rounds,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "runs")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--modes", nargs="+", choices=("frontier", "drain"),
                        default=("frontier", "drain"))
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    spec = read_input(INPUT_DIR / "input.txt")
    for case in args.cases:
        for mode in args.modes:
            result = run_case(case, mode, spec)
            folder = args.out / f"{case}__{mode}"
            folder.mkdir()
            (folder / "result.json").write_text(
                json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8",
            )
            print(case, mode, len(result["rounds"]), len(result["final"]), flush=True)


if __name__ == "__main__":
    main()
