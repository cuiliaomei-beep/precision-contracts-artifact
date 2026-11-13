"""Download, verify, and reproduce the complete Tomcat relation comparison."""

import argparse
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "experiments/tomcat"
ASSETS = ROOT / "release-assets"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, expected_sha256, expected_bytes):
    if path.stat().st_size != expected_bytes or sha256(path) != expected_sha256:
        raise ValueError(f"Size or SHA-256 mismatch: {path}")


def reference():
    return read(BASE / "results/reference_relations.json")


def summary():
    expected = reference()
    source = read(BASE / "inputs/source.json")
    verify_file(BASE / "inputs/tomcat.zip", source["archive_sha256"], source["archive_bytes"])
    for name, key in (("doop_original.dl", "rules_sha256"),
                      ("doop_all_relations.dl", "all_output_rules_sha256")):
        if sha256(BASE / "rules" / name) != source[key]:
            raise ValueError(f"Rule source differs: {name}")
    comparison = read(BASE / "results/full_comparison.json")
    if not comparison["all_relations_equal"] or not comparison["all_canonical_bytes_equal"]:
        raise ValueError("The recorded complete relation comparison did not pass")
    for case in ("phase_w1", "phase_w4"):
        measured = read(BASE / "results" / f"{case}_relations.json")
        if measured["full_state_sha256"] != expected["full_state_sha256"]:
            raise ValueError(f"Unexpected recorded state: {case}")
    return {
        "status": "PASS", "relations": expected["relation_count"],
        "tuples": expected["total_tuples"],
        "relation_bytes_per_run": expected["total_bytes"],
        "full_state_sha256": expected["full_state_sha256"],
        "dataset_archive_sha256": source["archive_sha256"],
    }


def fetch(args):
    manifest = read(BASE / "assets.json")
    args.directory.mkdir(parents=True, exist_ok=True)
    for asset in manifest["assets"]:
        path = args.directory / asset["file"]
        if not path.exists():
            repository = args.repo or manifest["repository"]
            tag = urllib.parse.quote(args.tag or manifest["release_tag"], safe="")
            base_url = args.base_url or f"https://github.com/{repository}/releases/download/{tag}"
            url = base_url.rstrip("/") + "/" + urllib.parse.quote(asset["file"], safe="")
            partial = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(url, timeout=120) as source, partial.open("wb") as output:
                shutil.copyfileobj(source, output)
            verify_file(partial, asset["sha256"], asset["bytes"])
            partial.replace(path)
        verify_file(path, asset["sha256"], asset["bytes"])
        print(f"Verified {asset['file']}", flush=True)
    return {"status": "PASS", "downloaded_or_checked": len(manifest["assets"])}


def verify_archives(directory):
    expected = reference()
    assets = read(BASE / "assets.json")["assets"]
    if len(assets) != 2:
        raise ValueError("Expected separate one-thread and four-thread archives")
    paths = [directory / asset["file"] for asset in assets]
    for path, asset in zip(paths, assets):
        verify_file(path, asset["sha256"], asset["bytes"])
    completed = []
    with tarfile.open(paths[0], "r|gz") as first, tarfile.open(paths[1], "r|gz") as second:
        archives = [first, second]
        for archive, asset in zip(archives, assets):
            member = archive.next()
            if member is None or member.name != "run.json" or not member.isfile():
                raise ValueError("Missing run identity in the result archive")
            metadata = json.load(archive.extractfile(member))
            if metadata != {"run": asset["run"], "threads": asset["threads"]}:
                raise ValueError("Unexpected run identity")
        for row in expected["relations"]:
            members = [archive.next() for archive in archives]
            for member in members:
                if (member is None or not member.isfile()
                        or member.name != "canonical/" + row["relation"] + ".tsv"
                        or member.size != row["bytes"]):
                    raise ValueError(f"Missing or unexpected relation: {row['relation']}")
            streams = [archive.extractfile(member) for archive, member in zip(archives, members)]
            digest = hashlib.sha256()
            count = size = 0
            previous = None
            for left, right in zip_longest(*streams):
                if left != right:
                    raise ValueError(f"Actual relation bytes differ: {row['relation']}")
                if not left.endswith(b"\n") or left.count(b"\t") != row["arity"] - 1:
                    raise ValueError(f"Malformed tuple: {row['relation']}")
                if previous is not None and left <= previous:
                    raise ValueError(f"Duplicate or unsorted tuple: {row['relation']}")
                previous = left
                digest.update(left)
                count += 1
                size += len(left)
            if (count, size, digest.hexdigest()) != (row["tuples"], row["bytes"], row["canonical_sha256"]):
                raise ValueError(f"Complete relation differs from the recorded result: {row['relation']}")
            completed.append({"relation": row["relation"], "tuples": count, "actual_bytes_equal": True})
        if any(archive.next() is not None for archive in archives):
            raise ValueError("Unexpected extra files in a result archive")
    report = {
        "status": "PASS", "archives_sha256": [asset["sha256"] for asset in assets],
        "relation_count": len(completed), "tuples_per_run": sum(row["tuples"] for row in completed),
        "all_actual_relation_bytes_equal": True, "relations": completed,
    }
    return report


def prepare_facts(destination):
    source = read(BASE / "inputs/source.json")
    archive = BASE / "inputs/tomcat.zip"
    verify_file(archive, source["archive_sha256"], source["archive_bytes"])
    if not (destination / "tomcat").exists():
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as compressed:
            for item in compressed.infolist():
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or path.parts[0] != "tomcat":
                    raise ValueError("Unexpected path in the input archive")
            compressed.extractall(destination)
    for row in source["input_files"]:
        path = destination / Path(row["path"].replace("\\", "/"))
        verify_file(path, row["sha256"], row["bytes"])
    return destination / "tomcat"


def linux_path(path):
    value = str(path.resolve()).replace("\\", "/")
    if len(value) > 2 and value[1:3] == ":/":
        return "/mnt/" + value[0].lower() + value[2:]
    return value


def native_command(program, arguments):
    if os.name == "nt":
        return ["wsl.exe", "--", program, *arguments]
    return [program, *arguments]


def canonicalize(outputs, destination):
    expected = reference()
    actual = {p.stem for p in outputs.glob("*.csv")}
    if actual != {row["relation"] for row in expected["relations"]}:
        raise ValueError("Missing or extra output relations")
    destination.mkdir()
    rows = []
    for row in expected["relations"]:
        path = destination / (row["relation"] + ".sorted.tsv")
        source = outputs / (row["relation"] + ".csv")
        subprocess.run(native_command("env", [
            "LC_ALL=C", "sort", "--parallel=1", "-S", "128M", "-T", linux_path(destination),
            "-o", linux_path(path), linux_path(source),
        ]), check=True)
        digest = hashlib.sha256()
        count = size = 0
        previous = None
        with path.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n") or line.count(b"\t") != row["arity"] - 1:
                    raise ValueError(f"Malformed tuple: {row['relation']}")
                if previous is not None and line <= previous:
                    raise ValueError(f"Duplicate or unsorted tuple: {row['relation']}")
                previous = line
                count += 1
                size += len(line)
                digest.update(line)
        if (count, size, digest.hexdigest()) != (row["tuples"], row["bytes"], row["canonical_sha256"]):
            raise ValueError(f"Analysis output differs: {row['relation']}")
        rows.append({"relation": row["relation"], "tuples": count, "sha256": digest.hexdigest()})
    return rows


def run(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.name):
        raise ValueError("Use a simple run name containing letters, digits, dots, underscores, or hyphens")
    directory = ROOT / "runs/tomcat" / args.name
    directory.mkdir(parents=True, exist_ok=False)
    facts = prepare_facts(ROOT / "downloads/tomcat")
    outputs = directory / "output"
    outputs.mkdir()
    if args.binary:
        solver = [linux_path(args.binary), linux_path(facts), linux_path(outputs),
                  str(args.threads), linux_path(directory / "phases.json")]
    else:
        solver = ["souffle", "-j", str(args.threads), "-F", linux_path(facts),
                  "-D", linux_path(outputs), linux_path(BASE / "rules/doop_all_relations.dl")]
    command = native_command("timeout", ["-k", "5s", str(args.timeout) + "s", *solver])
    started = time.perf_counter()
    with (directory / "stdout_stderr.log").open("wb") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout + 30)
    report = {
        "command": command, "threads": args.threads, "exit_code": process.returncode,
        "wall_seconds_including_output": time.perf_counter() - started,
        "complete": process.returncode == 0,
    }
    write(directory / "execution.json", report)
    if process.returncode:
        raise RuntimeError(f"Souffle did not complete; inspect {directory / 'stdout_stderr.log'}")
    report["relations"] = canonicalize(outputs, directory / "canonical")
    if args.binary:
        report["phases"] = read(directory / "phases.json")
    report["exact_reference_match"] = True
    write(directory / "execution.json", report)
    return {"status": "PASS", "run": args.name, "threads": args.threads, "relations": len(report["relations"])}


def compare(args):
    expected = reference()
    names = {row["relation"] + ".sorted.tsv" for row in expected["relations"]}
    for directory in (args.first, args.second):
        if {p.name for p in directory.glob("*.sorted.tsv")} != names:
            raise ValueError(f"Missing or extra relation files: {directory}")
    for row in expected["relations"]:
        left = args.first / (row["relation"] + ".sorted.tsv")
        right = args.second / (row["relation"] + ".sorted.tsv")
        with left.open("rb") as first, right.open("rb") as second:
            while True:
                a, b = first.read(1024 * 1024), second.read(1024 * 1024)
                if a != b:
                    raise ValueError(f"Actual relation bytes differ: {row['relation']}")
                if not a:
                    break
        verify_file(left, row["canonical_sha256"], row["bytes"])
    return {"status": "PASS", "relations": len(expected["relations"]), "actual_bytes_equal": True}


def build(args):
    args.directory.mkdir(parents=True, exist_ok=True)
    cpp = args.directory / "doop_snapshot.cpp"
    wrapper = args.directory / "souffle_phase_driver.cpp"
    shutil.copyfile(BASE / "rules/souffle_phase_driver.cpp", wrapper)
    binary = args.directory / "doop_phase_driver"
    generate = native_command("souffle", [
        "-g", linux_path(cpp), linux_path(BASE / "rules/doop_all_relations.dl"),
    ])
    compile_command = native_command("g++", [
        "-std=c++17", "-O3", "-fopenmp", "-DRAM_DOMAIN_SIZE=64",
        "-DUSE_NCURSES", "-DUSE_LIBZ", "-DUSE_SQLITE", linux_path(wrapper),
        "-ldl", "-lsqlite3", "-lz", "-lncurses", "-o", linux_path(binary),
    ])
    with (args.directory / "build.log").open("wb") as log:
        subprocess.run(generate, stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(compile_command, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = {"status": "PASS", "commands": [generate, compile_command], "binary": str(binary)}
    write(args.directory / "build.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary", help="check the included inputs, rules, and recorded comparison")
    download = commands.add_parser("fetch", help="download the two complete result archives")
    download.add_argument("--directory", type=Path, default=ASSETS)
    download.add_argument("--repo")
    download.add_argument("--tag")
    download.add_argument("--base-url", help="optional asset directory URL")
    verify = commands.add_parser("verify", help="read every result tuple and compare the actual bytes")
    verify.add_argument("--directory", type=Path, default=ASSETS)
    verify.add_argument("--output", type=Path)
    execute = commands.add_parser("run", help="evaluate the same rules with stock Souffle")
    execute.add_argument("--threads", type=int, choices=(1, 4), required=True)
    execute.add_argument("--name", required=True)
    execute.add_argument("--timeout", type=int, default=1800)
    execute.add_argument("--binary", type=Path, help="optional compiled phase driver built by the build command")
    compile_parser = commands.add_parser("build", help="generate stock Souffle C++ and compile the phase driver")
    compile_parser.add_argument("--directory", type=Path, default=ROOT / "build/tomcat")
    comparison = commands.add_parser("compare", help="compare two complete sorted output directories")
    comparison.add_argument("first", type=Path)
    comparison.add_argument("second", type=Path)
    args = parser.parse_args()
    if args.command == "summary":
        report = summary()
    elif args.command == "fetch":
        report = fetch(args)
    elif args.command == "verify":
        report = verify_archives(args.directory)
        if args.output:
            write(args.output, report)
    elif args.command == "run":
        report = run(args)
    elif args.command == "build":
        report = build(args)
    else:
        report = compare(args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
