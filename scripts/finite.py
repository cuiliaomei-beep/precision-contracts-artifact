"""Run and check the paper's finite semantic and placement experiments."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
from itertools import product
import json
from pathlib import Path
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "experiments/legacy_repair_20261016"
ADDRESS = ROOT / "experiments/precision_audit_20261016"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ADDRESS))

from artifact.core.ir import Program
from artifact.analysis.centralized_solver import CentralizedSolver
from artifact.analysis.domain import AbstractDomain
from artifact.analysis.projection import AnalysisGraph
from artifact.experiments.rq1 import RQ1Experiment, fingerprint, tagged_abstract, tokens
from artifact.experiments.rq2 import RQ2Experiment, STRATEGIES, canonical_states, state_hash
from alias_envelope import execute_envelope, compare


WORKERS = (1, 2, 4, 8)
SCHEDULES = tuple(range(42, 62))
CONFIGURATIONS = set(product(WORKERS, STRATEGIES, SCHEDULES))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(item):
    data = read(BASE / item["path"])
    require(fingerprint(data["program"]) == item["program_sha256"], "Input program differs: " + item["id"])
    require(data["program_sha256"] == item["program_sha256"], "Input index differs: " + item["id"])
    return data


def semantic_case(task):
    item, output, verify = task
    data = record(item)
    experiment = RQ1Experiment(Path(output) / "semantic-evaluation")
    result = experiment.test_program(
        Program.from_dict(data["program"]), data["id"],
        data.get("expected_observations"), data.get("expected_pending"),
    ).to_dict()
    require(result["status"] == "passed", "Semantic check failed: " + item["id"])
    if verify:
        stored = read(BASE / "rq1/programs" / (item["id"] + ".json"))
        fields = (
            "program_sha256", "concrete", "concrete_outcomes", "abstract", "oracle",
            "exact_match", "schedule_count", "state_count", "abstract_state",
            "abstract_state_sha256", "expected_pending", "pending_expectation_match",
            "expected_hand_observations", "hand_expectation_match",
        )
        for key in fields:
            require(fingerprint(result[key]) == fingerprint(stored[key]),
                    "Semantic result differs: " + item["id"] + ":" + key)
    else:
        save(Path(output) / "programs" / (item["id"] + ".json"), result)
    return dict(id=item["id"], family=item["family"], status="passed",
                exact=result["exact_match"], schedules=result["schedule_count"])


def layout_context(item, output):
    data = record(item)
    program = Program.from_dict(data["program"])
    graph = AnalysisGraph(program)
    kernel = CentralizedSolver(program, graph)
    reference = kernel.solve()
    experiment = RQ2Experiment(Path(output) / "layout-evaluation")
    return program, graph, kernel, reference, experiment


def verify_layout_case(task):
    item, output = task
    program, graph, kernel, reference, experiment = layout_context(item, output)
    expected = canonical_states(reference)
    summary = read(BASE / "rq2/programs" / (item["id"] + ".json"))
    require(summary["status"] == "passed", "Recorded layout failure: " + item["id"])
    require(summary["reference_states"] == expected, "Reference state differs: " + item["id"])
    path = BASE / summary["runs_path"]
    require(hashlib.sha256(path.read_bytes()).hexdigest() == summary["runs_sha256"],
            "Layout record file differs: " + item["id"])
    owners = {}
    for workers in WORKERS:
        for strategy in STRATEGIES:
            buckets = experiment._get_partition_fn(strategy, 42)(graph, workers)
            mapping = {node.id: worker for worker, bucket in enumerate(buckets) for node in bucket}
            require(set(mapping) == set(graph.nodes), "Incomplete ownership")
            owners[workers, strategy] = mapping
    seen = set()
    totals = Counter()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = row["workers"], row["partition"], row["schedule_seed"]
            require(key in CONFIGURATIONS and key not in seen, "Invalid or repeated layout configuration")
            seen.add(key)
            require(row["program"] == item["id"] and row["status"] == "passed" and not row["error"],
                    "Recorded layout run failed")
            require(row["program_sha256"] == item["program_sha256"], "Layout input differs")
            require(row["states"] == expected, "Full layout state differs: " + item["id"])
            actual = {name: AbstractDomain.from_dict(value) for name, value in row["states"].items()}
            require(not kernel.check_residual(actual), "Recorded layout state is open")
            require(row["ownership"] == owners[key[0], key[1]], "Recorded ownership differs")
            stats = row["statistics"]
            require(stats["converged"] and stats["residual_equations"] == 0, "Recorded execution incomplete")
            require(stats["num_workers"] == key[0] and stats["schedule_seed"] == key[2], "Execution labels differ")
            cut = sum(row["ownership"][edge.src.id] != row["ownership"][edge.dst.id] for edge in graph.edges)
            require(cut == row["cross_edges"] == stats["cross_partition_edges"], "Cross-owner edge count differs")
            require(row["messages"] == stats["total_messages"], "Message count differs")
            if key[2] % 2 == 0:
                require(stats["duplicate_messages"] == 0, "Unexpected duplicate deliveries")
            if not cut:
                require(stats["total_messages"] == stats["total_bytes"] == stats["duplicate_messages"] == 0,
                        "Messages without a cross-owner edge")
            totals.update(runs=1, messages=stats["total_messages"],
                          duplicates=stats["duplicate_messages"], cut_runs=int(cut > 0))
    require(seen == CONFIGURATIONS, "Missing layout configurations: " + item["id"])
    require(totals["messages"] == summary["serialized_messages"]
            and totals["duplicates"] == summary["duplicate_messages"]
            and totals["cut_runs"] == summary["runs_with_cut_edges"], "Per-program layout totals differ")
    return dict(id=item["id"], status="passed", **totals)


def run_layout_case(task):
    item, output = task
    output = Path(output)
    program, graph, kernel, reference, experiment = layout_context(item, output)
    path = output / "runs" / (item["id"] + ".jsonl.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    totals = Counter()
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=3) as stream:
        for workers in WORKERS:
            for strategy in STRATEGIES:
                buckets = experiment._get_partition_fn(strategy, 42)(graph, workers)
                for seed in SCHEDULES:
                    result = experiment.run_distributed_variant(
                        program, strategy, workers, seed, graph=graph, kernel=kernel,
                        reference=reference, buckets=buckets,
                    )
                    row = result.to_dict()
                    row.update(program=item["id"], program_sha256=item["program_sha256"],
                               reference_hash=state_hash(reference))
                    stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    require(result.status == "passed", "New layout run failed: " + item["id"])
                    totals.update(runs=1, messages=result.statistics["total_messages"],
                                  duplicates=result.statistics["duplicate_messages"],
                                  cut_runs=int(result.cross_partition_edges > 0))
    report = dict(id=item["id"], status="passed", reference_states=canonical_states(reference),
                  graph=graph.to_dict(), **totals)
    save(output / "programs" / (item["id"] + ".json"), report)
    return {key: value for key, value in report.items() if key not in ("reference_states", "graph")}


def alias_case(case):
    data = read(BASE / "corpus/rq1" / (case["id"] + ".json"))
    stored = read(BASE / "rq1/programs" / (case["id"] + ".json"))
    require(fingerprint(data["program"]) == case["baseline_program_sha256"], "Address input differs")
    envelope = execute_envelope(data["program"])
    require(fingerprint(envelope) == fingerprint(case["envelope"]), "Address envelope differs: " + case["id"])
    comparison = compare(envelope, stored["abstract"])
    require(comparison["exact"] and comparison["sound"], "Address envelope mismatch: " + case["id"])
    return dict(id=case["id"], status="passed", variants=envelope["enumerated_variants"],
                schedules=envelope["summed_schedule_count"])


def address_case(item):
    data = record(item)["program"]
    stored = read(BASE / "rq1/programs" / (item["id"] + ".json"))
    modes = (("baseline", False, False), ("precise_reads", True, False),
             ("precise_writes", False, True), ("precise_all", True, True))
    results = {}
    for name, reads, writes in modes:
        variant = deepcopy(data)
        for event in variant["events"]:
            if event["type"] == "RemoteRead" and reads or event["type"] == "RemoteWrite" and writes:
                event["attributes"].pop("possible_regions", None)
        program = Program.from_dict(variant)
        graph = AnalysisGraph(program)
        kernel = CentralizedSolver(program, graph)
        kernel.solve()
        observations = kernel.get_observations()
        require(set(observations) == set(stored["concrete"]), "Address-variant observation keys differ")
        exact = True
        for point, raw in observations.items():
            value = tagged_abstract(raw)
            concrete = tokens(stored["concrete"][point]["values"])
            may_taint = any(row["tainted"] for row in stored["concrete_outcomes"][point])
            covered = value["kind"] == "top" or concrete <= tokens(value["constants"])
            require(covered and (not may_taint or value["tainted"]), "Unsound address-information variant")
            exact = exact and value["kind"] != "top" and concrete == tokens(value["constants"]) and may_taint == value["tainted"]
        results[name] = bool(exact)
    return dict(id=item["id"], exact=results)


def execute(function, tasks, jobs):
    if jobs == 1:
        return [function(task) for task in tasks]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(function, tasks))


def make_output(command, requested):
    run_root = (ROOT / "runs").resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = requested.resolve() if requested else run_root / ("finite-" + command + "-" + stamp)
    require(output.is_relative_to(run_root), "Write new results beneath the repository runs directory")
    output.mkdir(parents=True, exist_ok=False)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "semantic", "layout", "addresses"))
    parser.add_argument("--limit", type=int, help="maximum number of programs in each selected group")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    require(args.jobs > 0 and (args.limit is None or args.limit > 0), "Use positive limits and job counts")
    output = make_output(args.command, args.out)
    started = time.perf_counter()
    summary = dict(command=args.command, subset=args.limit is not None, status="running")
    try:
        semantic = read(BASE / "corpus/rq1/index.json")["records"][:args.limit]
        layout = read(BASE / "corpus/rq2/index.json")["records"][:args.limit]
        aliases = read(ADDRESS / "ALIAS_ORACLE_AUDIT.json")["cases"][:args.limit]
        if args.command in ("semantic", "verify"):
            rows = execute(semantic_case, [(item, str(output), args.command == "verify") for item in semantic], args.jobs)
            save(output / "semantic.json", rows)
            summary["semantic"] = dict(programs=len(rows), exact=sum(r["exact"] for r in rows),
                                       schedules=sum(r["schedules"] for r in rows))
        if args.command in ("layout", "verify"):
            function = verify_layout_case if args.command == "verify" else run_layout_case
            rows = execute(function, [(item, str(output)) for item in layout], args.jobs)
            save(output / "layout.json", rows)
            summary["layout"] = dict(programs=len(rows), **{
                key: sum(r[key] for r in rows) for key in ("runs", "messages", "duplicates", "cut_runs")
            })
        if args.command in ("addresses", "verify"):
            rows = execute(alias_case, aliases, args.jobs)
            save(output / "address-envelopes.json", rows)
            summary["address_envelopes"] = dict(programs=len(rows), variants=sum(r["variants"] for r in rows),
                                                schedules=sum(r["schedules"] for r in rows))
        if args.command == "addresses":
            rows = execute(address_case, semantic, args.jobs)
            save(output / "address-variants.json", rows)
            summary["address_variants"] = dict(programs=len(rows), exact={
                mode: sum(row["exact"][mode] for row in rows)
                for mode in ("baseline", "precise_reads", "precise_writes", "precise_all")
            })
        summary.update(status="passed", seconds=time.perf_counter() - started)
    except Exception:
        summary.update(status="failed", seconds=time.perf_counter() - started, error=traceback.format_exc())
        save(output / "summary.json", summary)
        raise
    save(output / "summary.json", summary)
    print(json.dumps(dict(output=str(output), **summary), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
