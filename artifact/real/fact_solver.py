"""Exact finite-source propagation over exported, context-specific value-flow graphs.

The graph exporter, rather than this solver, is responsible for language/communication
semantics. Every graph edge denotes identity on P(SourceIDs); joins are set union.
The multiprocessing implementation retains the earlier experiment's local closure,
direct peer batches and BSP coordination, and additionally returns every node state.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import pickle
import queue
import random
import time
import traceback
from collections import deque
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def compile_graph(graph):
    nodes = graph["nodes"]
    if not nodes:
        raise ValueError("Empty graph cannot establish an end-to-end analysis")
    ids = sorted(str(n["id"]) for n in nodes)
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate graph node IDs")
    index = {v: i for i, v in enumerate(ids)}
    by_id = {str(n["id"]): n for n in nodes}
    sources = sorted({str(s) for n in nodes for s in n.get("seed", [])})
    if not sources:
        raise ValueError("No represented source; zero facts would be vacuous")
    if not any(n.get("sink_id") is not None for n in nodes):
        raise ValueError("No represented sink")
    source_bit = {s: 1 << i for i, s in enumerate(sources)}
    seeds = [sum(source_bit[s] for s in set(map(str, by_id[v].get("seed", [])))) for v in ids]
    edges = []
    edge_ids = set()
    for ei, edge in enumerate(graph["edges"]):
        eid = str(edge.get("id", ei))
        if eid in edge_ids:
            raise ValueError(f"Duplicate edge ID {eid}")
        edge_ids.add(eid)
        if str(edge["src"]) not in index or str(edge["dst"]) not in index:
            raise ValueError(f"Dangling edge {eid}")
        edges.append((index[str(edge["src"])], index[str(edge["dst"])], edge))
    out = [[] for _ in ids]
    incoming = [[] for _ in ids]
    for ei, (s, d, _) in enumerate(edges):
        out[s].append((d, ei))
        incoming[d].append(s)
    components = sorted({str(by_id[v].get("component", "unknown")) for v in ids})
    comp_index = {c: i for i, c in enumerate(components)}
    return dict(ids=ids, index=index, nodes=by_id, sources=sources, seeds=seeds,
                edges=edges, out=out, incoming=incoming, n=len(ids),
                comp=[comp_index[str(by_id[v].get("component", "unknown"))] for v in ids],
                components=components, graph_sha256=digest(graph))


def decode(compiled, masks):
    return {v: [s for j, s in enumerate(compiled["sources"]) if masks[i] & (1 << j)]
            for i, v in enumerate(compiled["ids"])}


def check_equations(compiled, masks):
    failures = []
    for v in range(compiled["n"]):
        expected = compiled["seeds"][v]
        for u in compiled["incoming"][v]:
            expected |= masks[u]
        if masks[v] != expected:
            failures.append(compiled["ids"][v])
    return failures


def solve_centralized(compiled):
    start = time.perf_counter()
    states = list(compiled["seeds"])
    work = deque(i for i, value in enumerate(states) if value)
    queued = set(work)
    updates = 0
    while work:
        u = work.popleft()
        queued.discard(u)
        for v, _ in compiled["out"][u]:
            new = states[v] | states[u]
            if new != states[v]:
                states[v] = new
                updates += 1
                if v not in queued:
                    work.append(v)
                    queued.add(v)
    failures = check_equations(compiled, states)
    if failures:
        raise AssertionError(f"Centralized residual equations: {failures[:5]}")
    return states, dict(wall_seconds=time.perf_counter() - start, state_updates=updates,
                        residual_equations=len(failures))


def solve_independent_reference(compiled):
    """Independent per-source graph reachability, not the worklist implementation."""
    masks = [0] * compiled["n"]
    for si, _ in enumerate(compiled["sources"]):
        bit = 1 << si
        todo = [v for v, seed in enumerate(compiled["seeds"]) if seed & bit]
        visited = set(todo)
        while todo:
            u = todo.pop()
            masks[u] |= bit
            for v, _ in compiled["out"][u]:
                if v not in visited:
                    visited.add(v)
                    todo.append(v)
    return masks


def partition(compiled, strategy, workers, seed):
    import numpy as np
    if strategy == "Forced-rpc-cut":
        if workers < 2:
            raise ValueError("Forced RPC cut needs at least two workers")
        adj = {}
        for s, d, edge in compiled["edges"]:
            if edge.get("kind") in {"rpc_request", "rpc_response"}:
                adj.setdefault(s, []).append(d)
                adj.setdefault(d, []).append(s)
        colors = {}
        for start in sorted(adj):
            if start in colors:
                continue
            colors[start] = 0
            todo = [start]
            while todo:
                u = todo.pop()
                for v in adj[u]:
                    if v in colors and colors[v] == colors[u]:
                        raise ValueError("RPC boundary graph not bipartite")
                    if v not in colors:
                        colors[v] = 1 - colors[u]
                        todo.append(v)
        return [colors.get(v, v % workers) for v in range(compiled["n"])]
    from artifact.real.rpc_partitions import PARTITIONERS
    graph = dict(n=compiled["n"], C=len(compiled["components"]),
                 comp=np.asarray(compiled["comp"], np.int32),
                 src=np.asarray([s for s, _, _ in compiled["edges"]], np.int32),
                 dst=np.asarray([d for _, d, _ in compiled["edges"]], np.int32))
    return PARTITIONERS[strategy](graph, workers, seed).tolist()


def _worker(wid, owned, seeds, edges, active, inboxes, coord, control, seed, duplicate):
    try:
        import psutil
        rng = random.Random(seed + wid * 1009)
        states = dict(zip(owned, seeds))
        work = [v for v in owned if states[v]]
        queued = set(work)
        outgoing = {v: [] for v in owned}
        for eid, u, v, owner in edges:
            outgoing[u].append((eid, v, owner))
        last_sent = {}
        peers = [p for p in active if p != wid]
        metrics = dict(updates=0, records=0, batches=0, payload_bytes=0, serialized_envelope_bytes=0,
                       control_envelopes=0, duplicate_records=0, rounds=0, compute_seconds=0.0)
        round_id = 0
        while True:
            round_id += 1
            metrics["rounds"] = round_id
            t0 = time.perf_counter()
            changed = False
            pending = {p: [] for p in peers}
            while work:
                idx = rng.randrange(len(work)) if seed else len(work) - 1
                u = work.pop(idx)
                queued.discard(u)
                for eid, v, owner in outgoing[u]:
                    if owner == wid:
                        new = states[v] | states[u]
                        if new != states[v]:
                            states[v] = new
                            metrics["updates"] += 1
                            changed = True
                            if v not in queued:
                                work.append(v)
                                queued.add(v)
                    elif states[u] != last_sent.get(eid, 0):
                        last_sent[eid] = states[u]
                        pending[owner].append((eid, v, states[u]))
            for p in peers:
                records = pending[p]
                if records:
                    rng.shuffle(records)
                    if duplicate:
                        extra = [r for i, r in enumerate(records) if i % 2 == 0]
                        metrics["duplicate_records"] += len(extra)
                        records = records + extra
                        rng.shuffle(records)
                    payload = pickle.dumps(records, protocol=5)
                    metrics["records"] += len(records)
                    metrics["batches"] += 1
                    metrics["payload_bytes"] += len(payload)
                    changed = True
                else:
                    payload = None
                    metrics["control_envelopes"] += 1
                envelope = (round_id, wid, payload)
                metrics["serialized_envelope_bytes"] += len(pickle.dumps(envelope, protocol=5))
                inboxes[p].put(envelope)
            metrics["compute_seconds"] += time.perf_counter() - t0
            received = [inboxes[wid].get(timeout=40) for _ in peers]
            rng.shuffle(received)
            seen = set()
            for rid, peer, payload in received:
                if rid != round_id or peer in seen:
                    raise AssertionError("BSP message/barrier mismatch")
                seen.add(peer)
                if payload is not None:
                    records = pickle.loads(payload)
                    rng.shuffle(records)
                    for _, v, mask in records:
                        if v not in states:
                            raise AssertionError("Message addressed to non-owner")
                        new = states[v] | mask
                        if new != states[v]:
                            states[v] = new
                            metrics["updates"] += 1
                            changed = True
                            if v not in queued:
                                work.append(v)
                                queued.add(v)
            # Report after all incoming round messages have been consumed.
            coord.put(("round", wid, round_id, bool(changed or work)))
            keep_going = control.get(timeout=40)
            if not keep_going:
                if work:
                    raise AssertionError("Premature termination with local work")
                break
        mi = psutil.Process().memory_info()
        metrics["peak_rss_mb"] = getattr(mi, "peak_wset", mi.rss) / 1e6
        coord.put(("done", wid, states, metrics))
    except BaseException:
        coord.put(("error", wid, traceback.format_exc()))


def solve_distributed(compiled, placement, schedule_seed=0, duplicate=False):
    if len(placement) != compiled["n"] or min(placement) < 0:
        raise ValueError("Invalid partition")
    active = sorted(set(placement))
    ctx = mp.get_context("spawn")
    inboxes = {w: ctx.Queue() for w in active}
    controls = {w: ctx.Queue() for w in active}
    coord = ctx.Queue()
    processes = []
    t0 = time.perf_counter()
    max_rounds = (compiled["n"] + len(compiled["edges"])) * (len(compiled["sources"]) + 1) + 3
    try:
        for w in active:
            own = [v for v in range(compiled["n"]) if placement[v] == w]
            edges = [(ei, s, d, placement[d]) for ei, (s, d, _) in enumerate(compiled["edges"])
                     if placement[s] == w]
            p = ctx.Process(target=_worker, args=(w, own, [compiled["seeds"][v] for v in own],
                        edges, active, inboxes, coord, controls[w], schedule_seed, duplicate))
            p.start()
            processes.append(p)
        rounds = 0
        while True:
            rounds += 1
            if rounds > max_rounds:
                raise RuntimeError("Round bound exceeded")
            flags = []
            workers_seen = set()
            for _ in active:
                msg = coord.get(timeout=50)
                if msg[0] == "error":
                    raise RuntimeError(msg[2])
                if msg[0] != "round" or msg[2] != rounds or msg[1] in workers_seen:
                    raise AssertionError(f"Bad coordinator message {msg[:3]}")
                workers_seen.add(msg[1])
                flags.append(msg[3])
            cont = any(flags)
            for control in controls.values():
                control.put(cont)
            if not cont:
                break
        merged = {}
        metrics = []
        for _ in active:
            msg = coord.get(timeout=50)
            if msg[0] == "error":
                raise RuntimeError(msg[2])
            if msg[0] != "done" or merged.keys() & msg[2].keys():
                raise AssertionError("Missing done message or duplicate state ownership")
            merged.update(msg[2])
            metrics.append(msg[3])
        for p in processes:
            p.join(timeout=8)
            if p.exitcode != 0:
                raise RuntimeError(f"Worker exit code {p.exitcode}")
        if set(merged) != set(range(compiled["n"])):
            raise AssertionError("Incomplete worker state map")
        states = [merged[v] for v in range(compiled["n"])]
        residuals = check_equations(compiled, states)
        if residuals:
            raise AssertionError(f"Distributed result not a fixed point: {residuals[:5]}")
        report = dict(wall_seconds=time.perf_counter() - t0, rounds=rounds,
                      active_workers=len(active), worker_metrics=metrics,
                      residual_equations=0, cut_edges=sum(placement[s] != placement[d]
                                                        for s, d, _ in compiled["edges"]))
        for key in ("updates", "records", "batches", "payload_bytes", "serialized_envelope_bytes",
                    "control_envelopes", "duplicate_records"):
            report[key] = sum(m[key] for m in metrics)
        report["peak_worker_rss_mb"] = max(m["peak_rss_mb"] for m in metrics)
        report["sum_peak_worker_rss_mb"] = sum(m["peak_rss_mb"] for m in metrics)
        report["memory_note"] = "sum of per-process peaks, not simultaneous system RSS"
        report["communication_note"] = "pickle protocol-5 record/envelope lengths; queue framing, startup graph transfer and coordinator transport excluded"
        report["coordinator_round_control_messages"] = 2 * rounds * len(active)
        report["coordinator_final_state_messages"] = len(active)
        return states, report
    except queue.Empty as exc:
        raise TimeoutError("Worker or coordinator message deadline exceeded") from exc
    finally:
        for p in processes:
            if p.is_alive():
                p.terminate()
            p.join(timeout=3)
        for q in list(inboxes.values()) + list(controls.values()) + [coord]:
            q.close()
            q.cancel_join_thread()


def sink_results(compiled, states):
    decoded = decode(compiled, states)
    results = []
    for nid in compiled["ids"]:
        node = compiled["nodes"][nid]
        if node.get("sink_id") is not None:
            results.append(dict(sink_id=node["sink_id"], node=nid, sources=decoded[nid],
                                method=node.get("method"), statement=node.get("statement"),
                                line=node.get("line")))
    return results


def witnesses(compiled, states):
    results = []
    for sink in sink_results(compiled, states):
        target = compiled["index"][sink["node"]]
        for source in sink["sources"]:
            bit = 1 << compiled["sources"].index(source)
            roots = [i for i, seed in enumerate(compiled["seeds"]) if seed & bit]
            todo = deque(roots)
            prev = {i: None for i in roots}
            via = {}
            while todo and target not in prev:
                u = todo.popleft()
                for v, ei in compiled["out"][u]:
                    if v not in prev:
                        prev[v] = u
                        via[v] = ei
                        todo.append(v)
            if target not in prev:
                raise AssertionError("Taint has no graph witness")
            path, edge_path = [], []
            u = target
            while u is not None:
                path.append(compiled["ids"][u])
                if u in via:
                    edge_path.append(compiled["edges"][via[u]][2])
                u = prev[u]
            results.append(dict(source=source, sink_id=sink["sink_id"],
                                nodes=list(reversed(path)), edges=list(reversed(edge_path)),
                                interpretation="possible explicit dataflow in the exported abstraction"))
    return results
