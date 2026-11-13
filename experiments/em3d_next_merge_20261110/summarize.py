"""Audit event delivery and summarize the measured next repair work."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from verify import HERE, REFERENCE, read_program, sha256


def processed_fact(event, program):
    _, _, _, node, value, _ = event
    initial = program.nv + program.na
    if node < program.nv:
        return ("V", node, value)
    if node >= initial:
        base, field = divmod(node - initial, len(program.fields))
        return ("H", base, field, value)
    return None  # Internal allocation constant, not a VarPointsTo row.


def classify(cause, direct, downstream_copies, downstream_store):
    key = tuple(cause[:2])
    if key in direct:
        return "next"
    if tuple(cause[:3]) in downstream_copies or key == downstream_store:
        return "downstream"
    return "other"


def summarize(path, verified, program, direct, downstream_copies, downstream_store):
    data = json.loads(path.read_text(encoding="utf-8"))
    label = path.parent.name
    def require(ok, message):
        if not ok:
            raise ValueError(f"{label}: {message}")

    def cause_kind(event):
        return classify(event[5], direct, downstream_copies, downstream_store)

    require(sha256(path) == verified["result_sha256"], "result changed after verification")
    partition = REFERENCE / f"{data['case']}.partition"
    require(sha256(partition) == data["partition_sha256"], "partition hash mismatch")
    require(list(map(int, partition.read_text().split())) == data["owners"], "wrong owner map")
    require(sha256(REFERENCE / "input.txt") == data["input_sha256"], "input hash mismatch")
    workers = set(data["owners"])
    pids = data["worker_pids"]
    require({int(w) for w in pids} == workers and len(set(pids.values())) == len(workers),
            "worker identities are not distinct")
    totals, trace, facts, stages = Counter(), [], set(), []
    by_scope = {name: Counter() for name in ("next", "downstream", "other")}
    for step, scan in zip(data["rounds"], verified["rounds"], strict=True):
        round_number = step["round"]
        if data["mode"] == "drain":
            require(all(e[1] != e[2] for e in step["outgoing"]), "drain left local work queued")
        stage_counts = Counter()
        target_records = []
        for row in step["workers"]:
            worker = row["worker"]
            records = row["records"]
            expected = [event for event in stages if event[2] == worker]
            require([r[0] for r in records[:len(expected)]] == expected,
                    "staged event failed ordered delivery")
            if round_number > 1:
                require(all(r[0][1] == worker for r in records[len(expected):]),
                        "unstaged remote event")
            counts = Counter(row["counters"])
            stage_counts.update(counts)
            outcome_counts = Counter()
            for event, status, replay in records:
                scope = cause_kind(event)
                outcome_counts[status] += 1
                by_scope[scope][status] += 1
                by_scope[scope]["replay_candidates"] += replay
                if status == "accepted":
                    fact = processed_fact(event, program)
                    if fact:
                        require(fact not in facts, "fact accepted twice")
                        facts.add(fact)
                if scope != "other":
                    target_records.append([worker, scope, event, status, replay])
            for status, counter in (
                ("accepted", "accepted_facts"), ("duplicate_fact", "duplicate_facts"),
                ("type_rejected", "type_rejected"), ("installed", "installed_edges"),
                ("duplicate_edge", "duplicate_edges"),
            ):
                require(outcome_counts[status] == counts[counter], f"bad count for {status}")
            require(sum(r[2] for r in records) == counts["replayed_facts"], "bad replay count")
        require(facts == {tuple(fact) for fact in step["checkpoint"]},
                "checkpoint disagrees with accepted facts")
        stages = step["outgoing"]
        for event in stages:
            if event[1] != event[2]:
                by_scope[cause_kind(event)][f"cross_{event[0]}"] += 1
        totals.update(stage_counts)
        if target_records or scan["next_heap_residuals"] or scan["next_load_residuals"]:
            trace.append(dict(
                round=round_number, full_scan=scan, events=target_records,
                staged_target_events=[e for e in stages if cause_kind(e) != "other"],
                whole_round_work=dict(stage_counts),
            ))
    return dict(
        case=data["case"], mode=data["mode"], workers=len(workers), rounds=len(data["rounds"]),
        partition_sha256=data["partition_sha256"], totals=dict(totals), by_scope=by_scope,
        next_heap_residual_rounds=verified["rounds_with_next_heap_residual"],
        next_load_residual_rounds=[
            row["round"] for row in verified["rounds"] if row["next_load_residuals"]
        ],
        worker_pid_and_ordered_delivery_checks_pass=True, trace=trace,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, nargs="?", default=HERE)
    args = parser.parse_args()
    root = args.folder
    validation = json.loads((root / "VERIFICATION.json").read_text(encoding="utf-8"))
    program = read_program(REFERENCE)
    graph = json.loads((REFERENCE / "graph.json").read_text(encoding="utf-8"))
    ids = {name: i for i, name in enumerate(graph["variables"])}
    direct = {
        ("store", i) for i, (_, _, f) in enumerate(program.stores) if f == program.next_field
    } | {("load", i) for i, (_, f, _) in enumerate(program.loads) if f == program.next_field}
    downstream_copies = {
        ("copy", ids[src], ids[dst]) for src, dst in (("v220", "v218"), ("v218", "v233"))
    }
    downstream_store = ("store", next(
        i for i, (src, _, f) in enumerate(program.stores)
        if src == ids["v233"] and program.fields[f] == "<Node$1Enumerate: Node current>"
    ))
    rows = [
        summarize(
            root / "runs" / f"{r['case']}__{r['mode']}" / "result.json",
            r, program, direct, downstream_copies, downstream_store,
        )
        for r in validation["runs"]
    ]
    report = dict(
        validation="PASS", run_count=len(rows),
        distinct_owner_maps=len({r["partition_sha256"] for r in rows}),
        direct_cause_tags=sorted(direct), downstream_copy_tags=sorted(downstream_copies),
        downstream_store_tag=downstream_store, runs=rows,
    )
    (root / "OBSERVATIONS.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (root / "CASE_TABLE.csv").open("w", encoding="utf-8", newline="") as stream:
        table = csv.writer(stream)
        table.writerow([
            "case", "mode", "workers", "rounds", "base_activations", "fact_candidates",
            "duplicate_facts", "cross_owner_events", "cross_owner_next_events",
            "next_heap_residual_rounds", "next_load_residual_rounds",
        ])
        for row in rows:
            scopes, totals = row["by_scope"], row["totals"]
            cross = lambda counter: counter["cross_E"] + counter["cross_F"]
            table.writerow([
                row["case"], row["mode"], row["workers"], row["rounds"],
                totals["base_activations"], totals["fact_candidates"], totals["duplicate_facts"],
                sum(cross(s) for s in scopes.values()), cross(scopes["next"]),
                " ".join(map(str, row["next_heap_residual_rounds"])),
                " ".join(map(str, row["next_load_residual_rounds"])),
            ])
    print(f"PASS: {len(rows)} isolated-worker runs, {report['distinct_owner_maps']} distinct maps")


if __name__ == "__main__":
    main()
