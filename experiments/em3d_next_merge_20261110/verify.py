"""Verify the isolated-worker Em3d baseline against the recorded Spark PAG oracle."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
REFERENCE = (
    HERE.parent / "precision_partition_20261017" / "measurements"
    / "em3d_final"
)


@dataclass
class Program:
    nv: int
    na: int
    fields: list
    masks: list
    variable_masks: list
    field_masks: list
    copies: list
    stores: list
    loads: list

    @property
    def next_field(self):
        return self.fields.index("<Node: Node next>")


def read_program(directory):
    tokens = iter(map(int, (directory / "input.txt").read_text().split()))
    nv, na, nf, nm = (next(tokens) for _ in range(4))
    masks = [{next(tokens) for _ in range(next(tokens))} for _ in range(nm)]
    variable_masks = [next(tokens) for _ in range(nv)]
    field_masks = [next(tokens) for _ in range(nf)]

    def read_rules(arity):
        count = next(tokens)
        return [tuple(next(tokens) for _ in range(arity)) for _ in range(count)]

    copies, stores, loads = read_rules(2), read_rules(3), read_rules(3)
    if next(tokens, None) is not None:
        raise ValueError("unexpected tokens after the recorded PAG rules")
    graph = json.loads((directory / "graph.json").read_text(encoding="utf-8"))
    fields = graph["fields"]
    if len(fields) != nf or graph["n_variables"] != nv or graph["n_allocations"] != na:
        raise ValueError("recorded input and graph disagree")
    return Program(nv, na, fields, masks, variable_masks, field_masks, copies, stores, loads)


def read_facts(rows, program, label):
    facts = set()
    limits = {"V": (program.nv, program.na), "H": (program.na, len(program.fields), program.na)}
    for row in rows:
        if not isinstance(row, (list, tuple)) or not row or row[0] not in limits:
            raise ValueError(f"{label}: invalid fact {row!r}")
        bounds = limits[row[0]]
        if len(row) != len(bounds) + 1 or any(
            type(value) is not int or not 0 <= value < bound
            for value, bound in zip(row[1:], bounds)
        ):
            raise ValueError(f"{label}: invalid numeric fact {row!r}")
        fact = tuple(row)
        if fact in facts:
            raise ValueError(f"{label}: duplicate fact {fact!r}")
        facts.add(fact)
    return facts


def read_oracle(path, program):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        kind, *args = line.split()
        rows.append((kind, *map(int, args)))
    return read_facts(rows, program, "oracle")


def read_souffle(directory, graph, program):
    variables = {name: i for i, name in enumerate(graph["variables"])}
    allocations = {name: i for i, name in enumerate(graph["allocations"])}
    fields = {name: i for i, name in enumerate(graph["fields"])}
    rows = []
    for line in (directory / "VarPointsTo.csv").read_text(encoding="utf-8").splitlines():
        variable, value = line.split("\t")
        rows.append(("V", variables[variable], allocations[value]))
    for line in (directory / "HeapPointsTo.csv").read_text(encoding="utf-8").splitlines():
        base, field, value = line.split("\t")
        rows.append(("H", allocations[base], fields[field], allocations[value]))
    return read_facts(rows, program, str(directory))


def residual_scan(program, facts):
    """Evaluate each original RHS against one recorded state, without applying outputs."""
    points_to = [set() for _ in range(program.nv)] + [{a} for a in range(program.na)]
    heap = defaultdict(set)
    for kind, *args in facts:
        if kind == "V":
            variable, value = args
            points_to[variable].add(value)
        else:
            base, field, value = args
            heap[base, field].add(value)

    missing = set()
    next_load_missing = set()
    next_field = program.next_field
    rhs_checks = Counter()
    candidate_pairs = Counter()
    type_rejected = Counter()
    base_visits = Counter()

    def check(kind, fact, mask, next_load=False):
        candidate_pairs[kind] += 1
        if mask >= 0 and fact[-1] not in program.masks[mask]:
            type_rejected[kind] += 1
        elif fact not in facts:
            missing.add(fact)
            if next_load:
                next_load_missing.add(fact)

    for source, target in program.copies:
        kind = "alloc" if source >= program.nv else "copy"
        rhs_checks[kind] += 1
        for value in points_to[source]:
            check(kind, ("V", target, value), program.variable_masks[target])
    for source, base, field in program.stores:
        rhs_checks["store"] += 1
        base_visits["store"] += len(points_to[base])
        for obj in points_to[base]:
            for value in points_to[source]:
                check("store", ("H", obj, field, value), program.field_masks[field])
    for base, field, target in program.loads:
        rhs_checks["load"] += 1
        base_visits["load"] += len(points_to[base])
        for obj in points_to[base]:
            for value in heap.get((obj, field), ()):
                check("load", ("V", target, value), program.variable_masks[target],
                      next_load=field == next_field)

    next_heap = sorted(
        fact for fact in missing if fact[0] == "H" and fact[2] == next_field
    )
    return {
        "rhs_checks": sum(rhs_checks.values()),
        "rhs_checks_by_kind": dict(rhs_checks),
        "candidate_pairs": sum(candidate_pairs.values()),
        "candidate_pairs_by_kind": dict(candidate_pairs),
        "type_rejected_candidate_pairs": dict(type_rejected),
        "base_visits_by_kind": dict(base_visits),
        "residual_count": len(missing),
        "residual_variable_count": sum(fact[0] == "V" for fact in missing),
        "residual_heap_count": sum(fact[0] == "H" for fact in missing),
        "next_heap_residual_count": len(next_heap),
        "next_heap_residuals": next_heap,
        "next_load_residuals": sorted(next_load_missing),
    }, missing


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_legacy_native(directory, program, oracle):
    rows = []
    for path in sorted(directory.glob("*.pts")):
        if read_oracle(path, program) != oracle:
            raise ValueError(f"{path}: legacy native solver and recorded .pts differ")
        stats = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        rows.append({
            "case": path.stem, "exact": True, "facts_sha256": sha256(path),
            "stats_sha256": sha256(path.with_suffix(".json")),
            "rounds": stats["rounds"], "transfer_attempts": stats["transfer_attempts"],
            "dynamic_copy_edges": stats["dynamic_copy_edges"],
        })
    return rows


def verify_run(path, program, oracle):
    data = json.loads(path.read_text(encoding="utf-8"))
    label = str(path)
    owners = data["owners"]
    if len(owners) != program.nv + program.na or any(
        type(owner) is not int or owner < 0 for owner in owners
    ):
        raise ValueError(f"{label}: invalid owner map")
    final = read_facts(data["final"], program, f"{label} final")
    if final != oracle:
        raise ValueError(
            f"{label}: final mismatch, missing {sorted(oracle - final)[:8]!r}, "
            f"extra {sorted(final - oracle)[:8]!r}"
        )
    final_scan, final_missing = residual_scan(program, final)
    if final_missing:
        raise ValueError(f"{label}: final state is open, residuals {sorted(final_missing)[:8]!r}")

    previous = set()
    rounds = []
    for expected_round, row in enumerate(data["rounds"], 1):
        if row["round"] != expected_round:
            raise ValueError(f"{label}: expected round {expected_round}, got {row['round']!r}")
        checkpoint = read_facts(row["checkpoint"], program, f"{label} round {expected_round}")
        if not previous <= checkpoint:
            raise ValueError(f"{label}: round {expected_round} lost prior facts")
        if not checkpoint <= final:
            raise ValueError(f"{label}: round {expected_round} has facts outside the final state")
        outgoing = row["outgoing"]
        if not isinstance(outgoing, list) or any(
            not isinstance(event, list) or len(event) < 3 for event in outgoing
        ):
            raise ValueError(f"{label}: malformed outgoing records in round {expected_round}")
        scan, _ = residual_scan(program, checkpoint)
        rounds.append({
            "round": expected_round,
            "checkpoint_facts": len(checkpoint),
            "new_checkpoint_facts": len(checkpoint - previous),
            "outgoing_records": len(outgoing),
            "cross_worker_outgoing_records": sum(event[1] != event[2] for event in outgoing),
            **scan,
        })
        previous = checkpoint

    if not rounds or previous != final or rounds[-1]["outgoing_records"]:
        raise ValueError(f"{label}: final checkpoint must equal final and have no outgoing records")

    return {
        "case": data["case"],
        "mode": data["mode"],
        "result": str(path.resolve()),
        "result_sha256": sha256(path),
        "workers_in_owner_map": len(set(owners)),
        "final_exact_and_closed": True,
        "final_variable_facts": sum(fact[0] == "V" for fact in final),
        "final_heap_facts": sum(fact[0] == "H" for fact in final),
        "final_full_scan": final_scan,
        "checkpoints_monotonic_and_within_final": True,
        "rounds_with_next_heap_residual": [
            row["round"] for row in rounds if row["next_heap_residual_count"]
        ],
        "rounds": rounds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, nargs="?", default=HERE, help="folder containing runs/")
    parser.add_argument("--reference-dir", type=Path, default=REFERENCE)
    parser.add_argument("--souffle-dir", type=Path, help="fresh stock Souffle outputs; default folder/souffle_fresh")
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args()

    directory = args.reference_dir.resolve()
    program = read_program(directory)
    oracle = read_oracle(directory / "reference.pts", program)
    graph = json.loads((directory / "graph.json").read_text(encoding="utf-8"))
    frozen_souffle = (
        HERE.parent / "precision_partition_20261017" / "runtime_baseline"
        / "spark_crosschecks" / "em3d_application_v2" / "output"
    )
    souffle_directories = [frozen_souffle, args.souffle_dir or args.folder / "souffle_fresh"]
    for outputs in souffle_directories:
        if read_souffle(outputs, graph, program) != oracle:
            raise ValueError(f"{outputs}: stock Souffle and recorded .pts differ")
    paths = sorted(args.folder.glob("runs/*/result.json"))
    if not paths:
        raise ValueError(f"{args.folder}: no runs/*/result.json")
    report = {
        "status": "PASS",
        "reference_dir": str(directory),
        "reference_sha256": {
            name: sha256(directory / name) for name in ("input.txt", "reference.pts", "graph.json")
        },
        "next_field": {"id": program.next_field, "name": program.fields[program.next_field]},
        "oracle_facts": len(oracle),
        "matching_souffle_outputs": [
            {
                "directory": str(outputs.resolve()),
                "sha256": {
                    name: sha256(outputs / name)
                    for name in ("VarPointsTo.csv", "HeapPointsTo.csv")
                },
            }
            for outputs in souffle_directories
        ],
        "matching_legacy_native_replays": verify_legacy_native(
            args.folder / "legacy_native_fresh", program, oracle,
        ),
        "run_count": len(paths),
        "runs": [verify_run(path, program, oracle) for path in paths],
    }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
