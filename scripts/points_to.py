"""Check and reproduce the three Java points-to examples used in the paper."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "experiments/precision_partition_20261017"
WORKLOADS = {"em3d": "em3d_application_v2", "bh": "bh_application", "mst": "mst_application"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def expected_relations(pag):
    variables = {(v, a) for v, values in pag["spark_variable_points_to"].items() for a in values}
    heap = {
        (row["base"], row["field"], a)
        for row in pag["spark_heap_points_to"] for a in row["points_to"]
    }
    return variables, heap


def read_relation(path, arity):
    rows = [tuple(line.split("\t")) for line in path.read_text(encoding="utf-8").splitlines()]
    if any(len(row) != arity for row in rows) or len(rows) != len(set(rows)):
        raise ValueError(f"Invalid relation: {path}")
    return set(rows)


def python_solution(pag):
    path = BASE / "real_workloads/verify_pag.py"
    spec = importlib.util.spec_from_file_location("pag_reference_solver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    variables, heap, rounds, visits = module.solve(pag)
    return (
        {(v, a) for v, values in variables.items() for a in values},
        {(base, field, a) for (base, field), values in heap.items() for a in values},
        dict(rounds=rounds, transfer_visits=visits),
    )


def souffle_solution(pag, folder, executable):
    facts, output = folder / "facts", folder / "output"
    facts.mkdir(parents=True)
    output.mkdir()
    allowed = [
        (node["id"], allocation)
        for node in pag["nodes"]
        for allocation in pag["allowed_by_type"][node["type"]]
    ]
    for name, rows in (
        ("Alloc", pag["alloc"]), ("Assign", pag["assign"]), ("Store", pag["store"]),
        ("Load", pag["load"]), ("Allowed", allowed),
    ):
        with (facts / f"{name}.facts").open("w", encoding="utf-8", newline="\n") as stream:
            for row in sorted({tuple(map(str, row)) for row in rows}):
                stream.write("\t".join(row) + "\n")
    rule = BASE / "runtime_baseline/spark_points_to.dl"
    command = [executable, "-j", "1", "-F", str(facts), "-D", str(output), str(rule)]
    subprocess.run(command, check=True)
    return read_relation(output / "VarPointsTo.csv", 2), read_relation(output / "HeapPointsTo.csv", 3)


def check(args):
    report = []
    for name, case in WORKLOADS.items():
        path = BASE / "real_workloads/runs" / case / "pag.json"
        pag = read_json(path)
        expected_v, expected_h = expected_relations(pag)
        actual_v, actual_h, stats = python_solution(pag)
        stored = BASE / "runtime_baseline/spark_crosschecks" / case / "output"
        if (actual_v, actual_h) != (expected_v, expected_h):
            raise ValueError(f"Python result differs from Spark for {name}")
        if (read_relation(stored / "VarPointsTo.csv", 2),
                read_relation(stored / "HeapPointsTo.csv", 3)) != (expected_v, expected_h):
            raise ValueError(f"Recorded Souffle result differs for {name}")
        if args.engine == "both":
            computed = souffle_solution(pag, args.output / name, args.souffle)
            if computed != (expected_v, expected_h):
                raise ValueError(f"New Souffle result differs for {name}")
        report.append(dict(
            name=name, variable_facts=len(actual_v), heap_facts=len(actual_h),
            exact=True, **stats,
        ))
    write_json(args.output / "results.json", dict(status="PASS", subjects=report))
    print(json.dumps(report, indent=2))


def fetch_sources(args):
    manifest = read_json(BASE / "real_workloads/JOLDEN_INPUT_MANIFEST.json")
    count = 0
    for row in manifest["files"]:
        relative = Path(*row["relative_path"].replace("\\", "/").split("/"))
        if relative.suffix != ".java" and relative.name != "README.md":
            continue
        target = args.output / relative
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]:
            count += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(row["url"], timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError(f"Source checksum differs: {relative}")
        target.write_bytes(data)
        count += 1
    print(f"Downloaded and checked {count} source files at {manifest['commit']}.")


def export(args):
    source = args.source_dir / args.subject / "src"
    java_sources = sorted(source.glob("*.java"))
    if not java_sources:
        raise FileNotFoundError(source)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    java_bin = Path(args.java_home) / "bin" if args.java_home else None
    suffix = ".exe" if os.name == "nt" else ""
    java = str(java_bin / ("java" + suffix)) if java_bin else shutil.which("java")
    javac = str(java_bin / ("javac" + suffix)) if java_bin else shutil.which("javac")
    if not java or not javac or not args.java8_rt.is_file():
        raise ValueError("Java 17 and a Java 8 rt.jar are required")
    app_classes, tool_classes = args.output / "app", args.output / "tool"
    app_classes.mkdir()
    tool_classes.mkdir()
    exporter = BASE / "real_workloads/SparkPagExporter.java"
    subprocess.run([javac, "--release", "8", "-d", str(app_classes), *map(str, java_sources)], check=True)
    subprocess.run([javac, "-encoding", "UTF-8", "-cp", args.classpath, "-d",
                    str(tool_classes), str(exporter)], check=True)
    config = dict(
        workload=args.subject, main_class={"em3d": "Em3d", "bh": "BH", "mst": "MST"}[args.subject],
        process_dirs=[str(app_classes.resolve())],
        classpath=str(app_classes.resolve()) + os.pathsep + str(args.java8_rt.resolve()),
        exclude=["java.*", "javax.*", "sun.*", "jdk.*"],
        spark_options=dict(apponly="true"),
    )
    write_json(args.output / "config.json", config)
    subprocess.run([
        java, "-Xmx3g", "-cp", str(tool_classes) + os.pathsep + args.classpath,
        "SparkPagExporter", str(args.output / "config.json"), str(args.output / "pag.json"),
    ], check=True)
    print(args.output / "pag.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("check")
    verify.add_argument("--engine", choices=("python", "both"), default="python")
    verify.add_argument("--souffle", default="souffle")
    verify.add_argument("--output", type=Path, default=ROOT / "runs/points-to")
    verify.set_defaults(action=check)
    fetch = sub.add_parser("fetch-sources")
    fetch.add_argument("--output", type=Path, default=ROOT / "subjects")
    fetch.set_defaults(action=fetch_sources)
    frontend = sub.add_parser("export")
    frontend.add_argument("subject", choices=WORKLOADS)
    frontend.add_argument("--source-dir", type=Path, default=ROOT / "subjects/jolden")
    frontend.add_argument("--java-home", default=os.environ.get("JAVA_HOME"))
    frontend.add_argument("--java8-rt", type=Path, required=True)
    frontend.add_argument("--classpath", required=True, help="Soot 4.5.0 and Gson 2.10.1 dependency classpath")
    frontend.add_argument("--output", type=Path, default=ROOT / "runs" / f"export-{time.time_ns()}")
    frontend.set_defaults(action=export)
    args = parser.parse_args()
    args.action(args)


if __name__ == "__main__":
    main()
