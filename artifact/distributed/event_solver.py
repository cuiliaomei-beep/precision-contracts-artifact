"""Exact legacy worker/message simulation, with actual randomized scheduling.

This executes worker-owned equation maps and serialized reliable messages. It is
not a claim that each of the 160,000 configurations launches OS processes.
"""
from __future__ import annotations
import hashlib
import json
import random
import time
from ..analysis.domain import AbstractDomain


class Worker:
    def __init__(self, worker_id, nodes):
        self.id, self.nodes = worker_id, list(nodes)
        self.local_facts = {n.id: AbstractDomain(reachable=False) for n in self.nodes}
        self.incoming = {n.id: {} for n in self.nodes}
        self.worklist = [n.id for n in self.nodes]
        self.queued = set(self.worklist)
        self.transfer_steps = self.state_updates = 0

    def add_local_work(self, node):
        nid = node.id if hasattr(node, "id") else node
        if nid not in self.local_facts:
            raise ValueError(f"Worker {self.id} does not own {nid}")
        if nid not in self.queued:
            self.worklist.append(nid)
            self.queued.add(nid)

    def has_work(self):
        return bool(self.worklist)

    def get_local_state(self):
        return dict(self.local_facts)

    def get_statistics(self):
        return dict(worker_id=self.id, nodes=len(self.nodes), transfer_steps=self.transfer_steps,
                    state_updates=self.state_updates)


class DistributedCoordinator:
    def __init__(self, analysis_graph, num_workers, partition_fn, schedule_seed=42,
                 duplicate_messages=False, max_steps=1_000_000):
        if num_workers < 1:
            raise ValueError("num_workers must be positive")
        self.analysis_graph, self.num_workers = analysis_graph, num_workers
        self.schedule_seed, self.duplicate_messages, self.max_steps = schedule_seed, duplicate_messages, max_steps
        buckets = partition_fn(analysis_graph, num_workers)
        if len(buckets) != num_workers:
            raise ValueError("Partition bucket count mismatch")
        self.owner = {}
        for wid, bucket in enumerate(buckets):
            for node in bucket:
                if node.id in self.owner or node.id not in analysis_graph.nodes:
                    raise ValueError("Duplicate or unknown node ownership")
                self.owner[node.id] = wid
        if set(self.owner) != set(analysis_graph.nodes):
            raise ValueError("Partition omitted nodes")
        self.workers = [Worker(w, bucket) for w, bucket in enumerate(buckets)]
        self.dependencies = {nid: [n.id for n in analysis_graph.get_dependencies(node)]
                             for nid, node in analysis_graph.nodes.items()}
        self.dependents = {nid: [n.id for n in analysis_graph.get_dependents(node)]
                           for nid, node in analysis_graph.nodes.items()}
        self.total_messages = self.total_bytes = self.iterations = self.duplicate_count = 0
        self.converged, self.total_time, self.trace_hash = False, 0.0, None
        self.residual_nodes = []

    def solve(self, transfer_fn, schedule_seed=None):
        if self.converged:
            raise RuntimeError("Coordinator instances execute exactly one run")
        start = time.perf_counter()
        if schedule_seed is not None:
            self.schedule_seed = schedule_seed
        rng = random.Random(self.schedule_seed)
        messages, last_sent, trace, steps = [], {}, hashlib.sha256(), 0
        while messages or any(w.has_work() for w in self.workers):
            steps += 1
            if steps > self.max_steps:
                raise RuntimeError("Step limit reached before quiescence")
            ready = [w for w in self.workers if w.has_work()]
            if ready and (not messages or rng.random() < 0.6):
                worker = rng.choice(ready)
                nid = worker.worklist.pop(rng.randrange(len(worker.worklist)))
                worker.queued.remove(nid)
                trace.update(f"L:{worker.id}:{nid};".encode())
                predecessors = {pred: (worker.local_facts[pred] if self.owner[pred] == worker.id else
                    worker.incoming[nid].get(pred, AbstractDomain(reachable=False)))
                    for pred in self.dependencies[nid]}
                value = transfer_fn(nid, predecessors)
                if not isinstance(value, AbstractDomain):
                    raise TypeError("Transfer must return AbstractDomain")
                old, new = worker.local_facts[nid], worker.local_facts[nid].join(value)
                worker.transfer_steps += 1
                if new == old:
                    continue
                worker.local_facts[nid], worker.state_updates = new, worker.state_updates + 1
                encoded = None
                for dst in self.dependents[nid]:
                    target = self.owner[dst]
                    if target == worker.id:
                        worker.add_local_work(dst)
                    else:
                        key = (nid, dst)
                        if last_sent.get(key) == new:
                            continue
                        last_sent[key] = new
                        if encoded is None:
                            encoded = json.dumps(new.to_dict(), sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False, allow_nan=False).encode("utf-8")
                        copies = 2 if self.duplicate_messages and rng.random() < 0.2 else 1
                        self.duplicate_count += copies - 1
                        for _ in range(copies):
                            messages.append((worker.id, target, nid, dst, encoded))
                            self.total_messages += 1
                            self.total_bytes += len(encoded)
            else:
                src_worker, target, pred, dst, payload = messages.pop(rng.randrange(len(messages)))
                trace.update(f"M:{src_worker}:{target}:{pred}:{dst};".encode())
                worker = self.workers[target]
                value = AbstractDomain.from_dict(json.loads(payload))
                old = worker.incoming[dst].get(pred, AbstractDomain(reachable=False))
                new = old.join(value)
                if new != old:
                    worker.incoming[dst][pred] = new
                    worker.add_local_work(dst)
        result = {}
        for worker in self.workers:
            result.update(worker.local_facts)
        if set(result) != set(self.analysis_graph.nodes):
            raise AssertionError("Incomplete final map")
        self.residual_nodes = [nid for nid in result if transfer_fn(nid, {
            pred: result[pred] for pred in self.dependencies[nid]}) != result[nid]]
        if self.residual_nodes:
            raise AssertionError(f"Unsatisfied global equations: {self.residual_nodes[:5]}")
        self.iterations, self.trace_hash = steps, trace.hexdigest()
        self.total_time, self.converged = time.perf_counter() - start, True
        return result

    def get_cross_partition_edges(self):
        return sum(self.owner[e.src.id] != self.owner[e.dst.id] for e in self.analysis_graph.edges)

    def get_load_balance(self):
        sizes = [len(w.nodes) for w in self.workers]
        average = sum(sizes) / len(sizes)
        return dict(avg_nodes_per_worker=average, max_nodes=max(sizes), min_nodes=min(sizes),
                    imbalance_ratio=(max(sizes) - min(sizes)) / average if average else 0)

    def get_statistics(self):
        return dict(execution_model="discrete-event worker/message simulation", num_workers=self.num_workers,
                    active_workers=sum(bool(w.nodes) for w in self.workers),
                    total_messages=self.total_messages, total_bytes=self.total_bytes,
                    byte_scope="UTF-8 serialized exact states; no physical network claim",
                    total_iterations=self.iterations, execution_time=self.total_time,
                    converged=self.converged, cross_partition_edges=self.get_cross_partition_edges(),
                    load_balance=self.get_load_balance(), schedule_seed=self.schedule_seed,
                    schedule_trace_sha256=self.trace_hash, duplicate_messages=self.duplicate_count,
                    residual_equations=len(self.residual_nodes),
                    workers=[w.get_statistics() for w in self.workers])
