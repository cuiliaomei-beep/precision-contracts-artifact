"""Analyze an actual exported bytecode graph and archive every topology state.

Example: python scripts/rpc_analysis.py --graph graph.json
    --expect expected.json --output runs/rpc-analysis
The expectations are a separately maintained JSON map {sink_label: is_tainted}.
"""
from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from artifact.real.fact_solver import (compile_graph, decode, digest, partition,
    solve_centralized, solve_independent_reference, solve_distributed, sink_results, witnesses)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expectation_check(sinks, expected):
    actual = {}
    for sink in sinks:
        label = str(sink["sink_id"])
        if label in actual:
            raise AssertionError(f"Duplicate sink label {label}; use unique callsite identities")
        actual[label] = bool(sink["sources"])
    if actual != expected:
        raise AssertionError(f"Semantic expectations differ: expected={expected}, actual={actual}")
    return actual


def run(graph_path, expected_path, output, workers, seeds, strategies):
    t0 = time.perf_counter()
    graph = json.loads(graph_path.read_text(encoding="utf-8-sig"))
    expected = json.loads(expected_path.read_text(encoding="utf-8-sig"))
    if not expected or any(type(v) is not bool for v in expected.values()):
        raise ValueError("Expected file must explicitly specify every sink as a boolean")
    metadata = graph.get("metadata", {})
    if metadata.get("status") != "complete_within_declared_scope":
        raise ValueError("Exporter must explicitly certify complete_within_declared_scope")
    failure_sidecar = graph_path.with_name(graph_path.name + ".failure.json")
    if failure_sidecar.exists() and failure_sidecar.stat().st_mtime >= graph_path.stat().st_mtime:
        raise ValueError("A later failed export makes this graph stale")
    if metadata.get("complete") is False or metadata.get("unsupported"):
        raise ValueError("Exporter reported incomplete/unsupported semantics")
    c = compile_graph(graph)
    reference, central_metrics = solve_centralized(c)
    independent = solve_independent_reference(c)
    if reference != independent:
        raise AssertionError("Worklist differs from independent per-source reachability")
    sinks = sink_results(c, reference)
    expectation_check(sinks, expected)
    paths = witnesses(c, reference)
    # Positive end-to-end witnesses must actually traverse both communication directions.
    for label, tainted in expected.items():
        if tainted:
            qualifying = [p for p in paths if str(p["sink_id"]) == label and
                          {"rpc_request", "rpc_response"} <= {e["kind"] for e in p["edges"]}]
            if not qualifying:
                raise AssertionError(f"Positive sink {label} has no request-and-response witness")
    state_map = decode(c, reference)
    save(output / "graph.json", graph)
    save(output / "expected.json", expected)
    save(output / "centralized.json", dict(states=state_map, sinks=sinks, metrics=central_metrics,
                                           states_sha256=digest(state_map)))
    save(output / "witnesses.json", paths)
    mutations = []
    if any(expected.values()):
        for kind in ("rpc_request", "rpc_response"):
            broken = copy.deepcopy(graph)
            broken["edges"] = [e for e in broken["edges"] if e.get("kind") != kind]
            bc = compile_graph(broken)
            bs, _ = solve_centralized(bc)
            actual = {str(s["sink_id"]): bool(s["sources"]) for s in sink_results(bc, bs)}
            detected = any(expected[k] and not actual.get(k, False) for k in expected)
            mutations.append(dict(removed_kind=kind, detected=detected, actual_sinks=actual,
                                  removed_edges=len(graph["edges"]) - len(broken["edges"])))
            save(output / "mutations" / f"remove_{kind}.json",
                 dict(graph=broken, states=decode(bc, bs), result=mutations[-1]))
            if not detected:
                raise AssertionError(f"Deleting {kind} did not expose missing end-to-end flow")
    runs = []
    configs = [(s, w, seed) for s in strategies for w in workers for seed in seeds]
    configs += [("Forced-rpc-cut", w, seed) for w in workers if w > 1 for seed in seeds]
    for number, (strategy, count, seed) in enumerate(configs, 1):
        rid = f"{number:03d}_{strategy}_w{count}_s{seed}"
        result = dict(id=rid, strategy=strategy, workers=count, seed=seed,
                      schedule="BSP with randomized local/receive order and finite duplicates" if seed else "BSP",
                      status="failed")
        try:
            tp = time.perf_counter()
            placement = partition(c, strategy, count, seed)
            result["partition_seconds"] = time.perf_counter() - tp
            states, metrics = solve_distributed(c, placement, schedule_seed=seed, duplicate=bool(seed))
            result["metrics"] = metrics
            result["placement"] = dict(zip(c["ids"], placement))
            result["states"] = decode(c, states)
            result["states_sha256"] = digest(result["states"])
            result["mismatched_nodes"] = [c["ids"][i] for i in range(c["n"]) if states[i] != reference[i]]
            expectation_check(sink_results(c, states), expected)
            if result["mismatched_nodes"]:
                raise AssertionError(f"State mismatch: {result['mismatched_nodes'][:5]}")
            result["status"] = "passed"
        except Exception:
            result["error"] = traceback.format_exc()
        save(output / "runs" / f"{rid}.json", result)
        runs.append({k: v for k, v in result.items() if k not in ("states", "placement")})
        print(f"[{number}/{len(configs)}] {rid}: {result['status']}", flush=True)
    summary = dict(schema_version=1, graph=str(graph_path.resolve()),
                   graph_sha256=c["graph_sha256"], expectation_file=str(expected_path.resolve()),
                   python=sys.version, platform=platform.platform(), nodes=c["n"], edges=len(c["edges"]),
                   source_ids=c["sources"], sinks=sinks, mutations=mutations,
                   centralized_equals_independent_reference=True, planned=len(configs), attempted=len(runs),
                   passed=sum(r["status"] == "passed" for r in runs),
                   failed=sum(r["status"] != "passed" for r in runs), runs=runs,
                   elapsed_seconds=time.perf_counter() - t0,
                   scope="Finite explicit provenance on exported query-projected bytecode graphs; BSP execution; no speedup claim")
    summary["status"] = "passed" if summary["passed"] == summary["planned"] else "failed"
    save(output / "summary.json", summary)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, required=True)
    ap.add_argument("--expect", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--workers", default="1,2,4,8")
    ap.add_argument("--seeds", default="0,1")
    ap.add_argument("--strategies", default="Single,Component,Random,SCC-aware,Communication-aware")
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.graph, args.expect, args.output, list(map(int, args.workers.split(","))),
                     list(map(int, args.seeds.split(","))), args.strategies.split(","))
    except Exception:
        save(args.output / "failure.json", dict(status="failed", error=traceback.format_exc()))
        raise
    print(json.dumps({k: result[k] for k in ("status", "planned", "passed", "failed", "nodes", "edges")}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
