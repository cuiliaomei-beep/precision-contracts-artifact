"""Check and replay the restricted source-derived Hadoop 3.3.6 fsync study."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "experiments/m0_hdfs_20261016"
AUDIT = BASE / "novelty_audit"
RUNTIME = BASE / "hdfs_runtime"
sys.path.insert(0, str(AUDIT))
sys.path.insert(0, str(BASE))
import finite_jimple
import source_projection
import validate_m1


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_records():
    summary = read(RUNTIME / "runtime_summary.json")
    assert summary["all_pass"]
    assert sha(RUNTIME / summary["mutation_manifest"]) == summary["mutation_manifest_sha256"]
    records = {}
    for key in ("original", "mutant"):
        path = RUNTIME / summary[key + "_run_record"]
        assert sha(path) == summary[key + "_run_record_sha256"]
        record = read(path)
        assert record["all_pass"] and len(record["client_result"]["cases"]) == 8
        assert record["client_result"]["pid"] != record["server_ready"]["pid"]
        assert record["server_off_result"]["pass"] and record["server_off_result"]["network_failure"]
        records[record["mode"]] = record
    return records


def source_check():
    for row in read(AUDIT / "SOURCE_MANIFEST.json"):
        assert sha(AUDIT / "sources" / row["entry"]) == row["sha256"]
    manifest = read(RUNTIME / "manifest.json")
    for relative, expected in manifest["source_sha256"].items():
        assert sha(RUNTIME / relative) == expected


def check_or_replay(output, replay):
    source_check()
    records = runtime_records()
    rows = []
    for mode, irname in (("original", "original"), ("holder_check_removed", "mutant")):
        ir = read(AUDIT / ("extracted_" + irname + ".json"))
        liveness = source_projection.Liveness(ir)
        for kind in ("RUNTIME", "HELD_OUT"):
            data = read(BASE / f"M1_{kind}_INPUT_{mode}.json")
            saved = read(BASE / f"M1_{kind}_PREDICTIONS_{mode}.json")
            projected_saved = read(BASE / f"M1_PROJECTED_{kind}_{mode}.json")
            assert saved == projected_saved
            expected_keys = {(f["id"], r["id"]) for f in data["families"] for r in f["requests"]}
            actual_keys = {(r["family"], r["request_ctx"]) for r in saved["results"]}
            assert actual_keys == expected_keys and len(saved["results"]) == len(expected_keys)
            assert saved["unknown_count"] == 0
            expected = {(f["id"], r["id"]): validate_m1.specification(f["heap"], r, mode != "original")
                        for f in data["families"] for r in f["requests"]}
            runtime_cases = {c["id"]: c for c in records[mode]["client_result"]["cases"]}
            for row in saved["results"]:
                assert row["exception_class"] == expected[row["family"], row["request_ctx"]]
                assert row["reached_persistBlocks"] == (row["exception_class"] is None)
                if kind == "RUNTIME":
                    assert (row["exception_class"] or "normal") == runtime_cases[row["request_ctx"]]["outcome"]
            detail = dict(mode=mode, input_kind=kind, rows=len(saved["results"]),
                          input_sha256=sha(BASE / f"M1_{kind}_INPUT_{mode}.json"),
                          saved_equal=True, specification_equal=True)
            if replay:
                full, full_cost = source_projection.run(ir, data)
                projected, projected_cost = source_projection.run(ir, data, liveness)
                assert full == saved and projected == saved
                save(output / f"{kind}_{mode}_full.json", full)
                save(output / f"{kind}_{mode}_projected.json", projected)
                detail.update(fresh_equal=True, full=full_cost, projected=projected_cost)
            rows.append(detail)
        if replay:
            save(output / f"liveness_{mode}.json", liveness.certificate())
    return dict(status="passed", rows=rows, total_rows=sum(r["rows"] for r in rows),
                real_runtime_cases=16, scope="Restricted finite heap and explicit library contracts; not whole-HDFS analysis")


def fetch(deps):
    rows = read(RUNTIME / "dependencies.json")["dependencies"]
    downloaded, unavailable = [], []
    for row in rows:
        target = deps / row["relative_path"]
        if target.exists() and sha(target) == row["sha256"]:
            continue
        if not row.get("url"):
            unavailable.append(row["relative_path"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".partial")
        with urllib.request.urlopen(row["url"], timeout=60) as response:
            temporary.write_bytes(response.read())
        assert sha(temporary) == row["sha256"], "Dependency hash mismatch: " + str(target)
        temporary.replace(target)
        downloaded.append(row["relative_path"])
    return dict(status="source_build_required" if unavailable else "passed", downloaded=downloaded,
                unavailable=unavailable, directory=str(deps))


def dependency_check(hadoop, deps):
    for row in read(RUNTIME / "dependencies.json")["dependencies"]:
        relative = row["relative_path"]
        path = deps / Path(relative).name if relative.startswith("extra/") else hadoop / relative
        assert path.exists(), "Missing pinned dependency: " + str(path)
        assert sha(path) == row["sha256"], "Dependency hash mismatch: " + str(path)


def execute_runtime(output, hadoop, java_home, deps):
    if sys.platform != "linux":
        raise RuntimeError("Use Linux or WSL for the real MiniDFSCluster run")
    if not hadoop or not java_home:
        raise ValueError("Set --hadoop-home and --java-home (or HADOOP_HOME and JAVA_HOME)")
    dependency_check(hadoop, deps)
    work = output / "harness"
    work.mkdir()
    for name in ("run_runtime.py", "build_mutant.py", "summarize.py", "run_all.py", "log4j.properties", "dependencies.json"):
        shutil.copy2(RUNTIME / name, work / name)
    shutil.copytree(RUNTIME / "src", work / "src")
    shutil.copytree(deps, work / "deps")
    environment = dict(os.environ, HADOOP_HOME=str(hadoop.resolve()), JAVA_HOME=str(java_home.resolve()))
    with (output / "execution.log").open("w", encoding="utf-8") as log:
        process = subprocess.run([sys.executable, str(work / "run_all.py")], env=environment,
                                 stdout=log, stderr=subprocess.STDOUT, timeout=900)
    assert process.returncode == 0, "Real HDFS run failed; inspect execution.log"
    summary = read(work / "runtime_summary.json")
    return dict(status="passed", real_runtime_cases=summary["original_cases"] + summary["mutant_cases"],
                paired_changed_cases=summary["paired_changed_cases"], harness=str(work))


def export_ir(output, hadoop, java_home, soot_cp, variant, mutation_classes):
    if not hadoop or not java_home or not soot_cp:
        raise ValueError("Export requires --hadoop-home, --java-home and --soot-cp")
    suffix = ".exe" if os.name == "nt" else ""
    java, javac = (str(java_home / "bin" / (name + suffix)) for name in ("java", "javac"))
    classes = output / "classes"
    classes.mkdir()
    subprocess.run([javac, "-encoding", "UTF-8", "-cp", soot_cp, "-d", str(classes),
                    str(AUDIT / "HdfsIrExporter.java")], check=True)
    paths = sorted((hadoop / "share/hadoop/common").glob("*.jar"))
    paths += sorted((hadoop / "share/hadoop/common/lib").glob("*.jar"))
    paths += sorted((hadoop / "share/hadoop/hdfs").glob("*.jar"))
    paths += sorted((hadoop / "share/hadoop/hdfs/lib").glob("*.jar"))
    if variant == "mutant":
        if not mutation_classes:
            raise ValueError("Mutant IR export requires --mutation-classes")
        paths.insert(0, mutation_classes)
    config = dict(classpath=os.pathsep.join(map(str, paths)), methods=read(AUDIT / "methods.json"), variant=variant)
    save(output / "export_config.json", config)
    command = [java, "-Xmx3g", "-cp", os.pathsep.join((str(classes), soot_cp)), "HdfsIrExporter",
               str(output / "export_config.json"), str(output / "extracted.json")]
    with (output / "export.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
    ir = read(output / "extracted.json")
    return dict(status="passed", methods=len(ir["methods"]), units=sum(len(m["units"]) for m in ir["methods"]),
                ir_sha256=sha(output / "extracted.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "replay", "fetch", "runtime", "export"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--deps", type=Path)
    parser.add_argument("--hadoop-home", type=Path, default=os.environ.get("HADOOP_HOME"))
    parser.add_argument("--java-home", type=Path, default=os.environ.get("JAVA_HOME"))
    parser.add_argument("--soot-cp", help="Classpath containing Soot 4.5.0, Gson, and Soot dependencies")
    parser.add_argument("--variant", choices=("original", "mutant"), default="original")
    parser.add_argument("--mutation-classes", type=Path)
    args = parser.parse_args()
    output = (args.output or ROOT / "runs" / ("hdfs-" + args.action + "-" + str(time.time_ns()))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    deps = (args.deps or ROOT / "dependencies/hdfs/extra").resolve()
    if args.action in ("check", "replay"):
        result = check_or_replay(output, args.action == "replay")
    elif args.action == "fetch":
        result = fetch((args.deps or ROOT / "dependencies/hdfs").resolve())
    elif args.action == "runtime":
        result = execute_runtime(output, args.hadoop_home, args.java_home, deps)
    else:
        result = export_ir(output, args.hadoop_home, args.java_home, args.soot_cp, args.variant, args.mutation_classes)
    save(output / "report.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
