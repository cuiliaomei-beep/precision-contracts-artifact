"""Inspect, replay, and rebuild the eight RPC examples used in the paper."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "experiments/e2e_20261016"
sys.path.insert(0, str(ROOT))
from artifact.real.fact_solver import (
    compile_graph, decode, digest, solve_centralized, solve_independent_reference, sink_results, witnesses,
)
from scripts.rpc_analysis import run


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_cases(selection):
    cases = sorted(path for path in (BASE / "results").glob("*/*") if path.is_dir())
    if selection:
        cases = [path for path in cases if path.relative_to(BASE / "results").as_posix() == selection]
    if not cases:
        raise ValueError("No matching RPC case")
    return cases


def check(selection):
    rows = []
    for framework in ("dubbo", "grpc"):
        subject = BASE / framework
        manifest = read(subject / "manifest.json")
        for key in ("source_hashes", "generated_hashes"):
            for relative, expected_hash in manifest.get(key, {}).items():
                assert sha(subject / relative.replace("\\", "/")) == expected_hash
        runtime_data = read(subject / "runtime_results.json")
        if framework == "dubbo":
            assert runtime_data["all_pass"]
            for case in runtime_data["scenarios"]:
                assert case["pass"] and case["separate_jvms"] and case["actual_sinks"] == case["expected_sinks"]
        else:
            assert runtime_data["all_passed"] and runtime_data["successful"] == 4 and runtime_data["failed"] == 0
            assert all(case["passed"] and case["server_pid"] != case["client_pid"] for case in runtime_data["cases"])
    for folder in selected_cases(selection):
        graph = read(folder / "graph.json")
        compiled = compile_graph(graph)
        reference, _ = solve_centralized(compiled)
        assert reference == solve_independent_reference(compiled)
        reference_map = decode(compiled, reference)
        saved = read(folder / "centralized.json")
        assert reference_map == saved["states"]
        expected = read(folder / "expected.json")
        actual = {str(s["sink_id"]): bool(s["sources"]) for s in sink_results(compiled, reference)}
        assert actual == expected
        paths = witnesses(compiled, reference)
        assert paths == read(folder / "witnesses.json")
        for label, tainted in expected.items():
            if tainted:
                assert any(str(p["sink_id"]) == label and
                           {"rpc_request", "rpc_response"} <= {e["kind"] for e in p["edges"]} for p in paths)
        seen = set()
        run_paths = sorted((folder / "runs").glob("*.json"))
        for path in run_paths:
            result = read(path)
            key = (result["strategy"], result["workers"], result["seed"])
            assert key not in seen
            seen.add(key)
            assert result["status"] == "passed" and not result["mismatched_nodes"]
            assert result["states"] == reference_map
            assert result["states_sha256"] == digest(reference_map)
            assert set(result["placement"]) == set(reference_map)
        assert len(run_paths) == 46
        rows.append(dict(case=folder.relative_to(BASE / "results").as_posix(), runs=len(run_paths),
                         graph_sha256=sha(folder / "graph.json"), exact=True))
    return dict(status="passed", cases=len(rows), configurations=sum(r["runs"] for r in rows), rows=rows)


def analyze(selection, output, quick):
    rows = []
    for folder in selected_cases(selection):
        case = folder.relative_to(BASE / "results")
        workers = [1, 2] if quick else [1, 2, 4, 8]
        seeds = [0] if quick else [0, 1]
        strategies = ["Single", "Random"] if quick else [
            "Single", "Component", "Random", "SCC-aware", "Communication-aware",
        ]
        report = run(folder / "graph.json", folder / "expected.json", output / case,
                     workers, seeds, strategies)
        assert report["status"] == "passed"
        rows.append(dict(case=case.as_posix(), configurations=report["passed"]))
    return dict(status="passed", cases=len(rows), configurations=sum(r["configurations"] for r in rows), rows=rows)


def dependency_rows(framework):
    return read(BASE / "dependencies.json")[framework]["dependencies"]


def fetch(framework, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if framework == "grpc":
        asset = read(BASE / "dependencies.json")["grpc"]["runtime_asset"]
        missing_snapshot = [r for r in dependency_rows(framework) if "SNAPSHOT" in r["filename"]
                            and not ((destination / r["filename"]).exists() and sha(destination / r["filename"]) == r["sha256"])]
        if missing_snapshot:
            archive = ROOT / "release-assets" / asset["file"]
            if not archive.exists():
                archive = destination / asset["file"]
                if not archive.exists():
                    subprocess.run(["gh", "release", "download", asset["release_tag"], "--repo", asset["repository"],
                                    "--pattern", asset["file"], "--dir", str(destination)], check=True)
            assert sha(archive) == asset["sha256"], "Snapshot archive hash mismatch"
            with zipfile.ZipFile(archive) as bundle:
                assert set(bundle.namelist()) == set(asset["contents"]) | {"LICENSE", "NOTICE.txt"}
                for row in missing_snapshot:
                    content = bundle.read(row["filename"])
                    assert hashlib.sha256(content).hexdigest() == row["sha256"]
                    (destination / row["filename"]).write_bytes(content)
                for name in ("LICENSE", "NOTICE.txt"):
                    (destination / ("grpc-runtime-" + name)).write_bytes(bundle.read(name))
    build = []
    for row in dependency_rows(framework):
        target = destination / row["filename"]
        if target.exists() and sha(target) == row["sha256"]:
            continue
        if not row.get("url"):
            build.append(row["filename"])
            continue
        partial = target.with_suffix(target.suffix + ".partial")
        with urllib.request.urlopen(row["url"], timeout=120) as source, partial.open("wb") as out:
            shutil.copyfileobj(source, out)
        if sha(partial) != row["sha256"]:
            raise ValueError("Dependency bytes differ: " + row["filename"])
        partial.replace(target)
    return dict(status="source_build_required" if build else "passed", source_build_jars=build)


def java_tools(home):
    suffix = ".exe" if os.name == "nt" else ""
    if home:
        java, javac = home / ("bin/java" + suffix), home / ("bin/javac" + suffix)
    else:
        java, javac = shutil.which("java"), shutil.which("javac")
    if not java or not javac:
        raise FileNotFoundError("Supply --java-home for a JDK")
    return str(java), str(javac)


def jars(framework, folder):
    result = []
    for row in dependency_rows(framework):
        path = folder / row["filename"]
        if not path.exists() or sha(path) != row["sha256"]:
            raise ValueError("Missing or different dependency: " + str(path))
        result.append(path.resolve())
    return result


def sink_values(text):
    output = {}
    for line in text.splitlines():
        if line.startswith(("E2E_SINK\t", "SINK\t")):
            _, label, value = line.split("\t", 2)
            if label in output:
                raise ValueError("Duplicate sink label " + label)
            output[label] = value
    return output


def runtime(framework, dep_dir, java_home, output, selection):
    java, javac = java_tools(java_home)
    dependencies = jars(framework, dep_dir)
    subject = BASE / framework
    classes = output / "classes"
    classes.mkdir(parents=True)
    source_files = sorted((subject / "src").rglob("*.java"))
    if framework == "grpc":
        source_files += sorted((subject / "generated").rglob("*.java"))
        source_files += sorted((subject / "compile_support").rglob("*.java"))
    cp = os.pathsep.join(map(str, dependencies))
    command = ["-proc:none", "-g", "-encoding", "UTF-8", "-cp", cp, "-d", str(classes)]
    if framework == "grpc":
        command += ["--release", "8"]
    command += [str(p.resolve()) for p in source_files]
    argfile = output / "javac.args"
    argfile.write_text("\n".join('"' + part.replace("\\", "/") + '"' for part in command) + "\n", encoding="utf-8")
    compiled = subprocess.run([javac, "@" + str(argfile)], capture_output=True, text=True)
    (output / "compile.log").write_text(compiled.stdout + compiled.stderr, encoding="utf-8")
    compiled.check_returncode()
    manifest = read(subject / "manifest.json")
    runtime_data = read(subject / "runtime_results.json")
    prior = {r.get("name", r.get("scenario")): r for r in runtime_data.get("scenarios", runtime_data.get("cases", []))}
    actual_cp = os.pathsep.join([str(classes.resolve()), cp])
    rows = []
    for scenario in manifest["scenarios"]:
        name = scenario.get("name", scenario.get("id"))
        if selection and selection != framework + "/" + name:
            continue
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if framework == "dubbo":
            server_class = "e2e.dubbo.Provider"
            server_args = ["constant" if scenario["derived_provider"] else "original", str(port)]
            marker = scenario["runtime_source_value"]
            client_args = [marker, str(port)]
            expected = scenario["expected_runtime_sinks"]
        else:
            server_class = scenario["server_class"]
            server_args = [str(port)]
            marker = prior[name]["marker"]
            client_args = [str(port), marker]
            expected = scenario["expected_sink_values"]
        server_command = [java, "-cp", actual_cp, server_class, *server_args]
        client_command = [java, "-cp", actual_cp, scenario["consumer_class"], *client_args]
        log_path = output / (name + "_server.log")
        with log_path.open("w", encoding="utf-8") as server_log:
            server = subprocess.Popen(server_command, stdin=subprocess.PIPE, stdout=server_log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 40
                while "READY" not in log_path.read_text(encoding="utf-8", errors="replace"):
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Provider failed to start: " + str(log_path))
                    time.sleep(.05)
                client = subprocess.Popen(client_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                text, _ = client.communicate(timeout=90)
                (output / (name + "_client.log")).write_text(text, encoding="utf-8")
                actual = sink_values(text)
                assert client.returncode == 0 and actual == expected and client.pid != server.pid
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
        unavailable = subprocess.run(client_command, capture_output=True, text=True, timeout=90)
        (output / (name + "_provider_absent.log")).write_text(unavailable.stdout + unavailable.stderr, encoding="utf-8")
        assert unavailable.returncode != 0 and not sink_values(unavailable.stdout)
        rows.append(dict(case=name, server_pid=server.pid, client_pid=client.pid,
                         actual=actual, expected=expected, provider_absent_exit=unavailable.returncode, passed=True))
    if not rows:
        raise ValueError("No selected runtime case")
    runtime_manifest = dict(manifest, class_dir=str(classes.resolve()),
                            classpath_dependencies=list(map(str, dependencies)))
    runtime_manifest.pop("classpath_file", None)
    save(output / "manifest.json", runtime_manifest)
    return dict(status="passed", framework=framework, cases=rows, dependencies={p.name: sha(p) for p in dependencies})


def export(manifest_path, java_home, soot_cp, output, selection):
    if not manifest_path or not soot_cp:
        raise ValueError("Export requires --manifest from a fresh runtime run and --soot-cp")
    manifest = read(manifest_path)
    if "classpath_file" in manifest:
        raise ValueError("Use a fresh portable runtime manifest; saved manifests retain historical paths")
    java, javac = java_tools(java_home)
    classes = output / "classes"
    classes.mkdir()
    exporter = BASE / "frontend/ValueFlowExporter.java"
    subprocess.run([javac, "-encoding", "UTF-8", "-cp", soot_cp, "-d", str(classes), str(exporter)], check=True)
    cp = os.pathsep.join([manifest["class_dir"], *manifest["classpath_dependencies"]])
    framework = "grpc" if "grpc" in manifest["framework"].lower() else "dubbo"
    rows = []
    for scenario in manifest["scenarios"]:
        name = scenario.get("id", scenario.get("name"))
        if selection and selection != framework + "/" + name:
            continue
        config = dict(protocol=framework, classpath=cp, process_dirs=[manifest["class_dir"]],
                      entry=scenario["consumer_entry"],
                      handler=scenario.get("handler_entry", scenario.get("provider_method")),
                      registration_class=scenario.get("server_class", "e2e.dubbo.Provider"),
                      rpc_owner="io.grpc.examples.helloworld.GreeterGrpc$GreeterBlockingStub" if framework == "grpc" else "org.apache.dubbo.samples.api.GreetingsService",
                      rpc_method="sayHello" if framework == "grpc" else "sayHi",
                      manifest_sha256=sha(manifest_path),
                      resolution_method="Deployment manifest checked against compiled RPC signatures, handler class and bootstrap registration")
        if framework == "grpc":
            config["protobuf"] = dict(request=dict(class_="io.grpc.examples.helloworld.HelloRequest", field="name"),
                                      response=dict(class_="io.grpc.examples.helloworld.HelloReply", field="message"),
                                      descriptor_class="io.grpc.examples.helloworld.HelloWorldProto", service="Greeter", method="SayHello")
            for key in ("request", "response"):
                config["protobuf"][key]["class"] = config["protobuf"][key].pop("class_")
        config_path, graph_path = output / (name + ".config.json"), output / (name + ".graph.json")
        save(config_path, config)
        with (output / (name + ".export.log")).open("w", encoding="utf-8") as log:
            subprocess.run([java, "-Xmx3g", "-cp", os.pathsep.join((str(classes), cp, soot_cp)),
                            "ValueFlowExporter", str(config_path), str(graph_path)],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
        graph = read(graph_path)
        assert graph["metadata"]["status"] == "complete_within_declared_scope"
        rows.append(dict(case=framework + "/" + name, graph=str(graph_path), graph_sha256=sha(graph_path)))
    assert rows, "No selected export case"
    return dict(status="passed", rows=rows, exporter_sha256=sha(exporter))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "analyze", "runtime", "fetch", "export"))
    parser.add_argument("--case", help="One case, for example dubbo/positive")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--framework", choices=("dubbo", "grpc"), default="grpc")
    parser.add_argument("--deps", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--soot-cp", help="Classpath containing Soot 4.5.0, Gson, and Soot dependencies")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or ROOT / "runs" / ("rpc-" + args.action + "-" + str(time.time_ns()))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.action == "check":
        result = check(args.case)
    elif args.action == "analyze":
        result = analyze(args.case, output, args.quick)
    elif args.action == "export":
        result = export(args.manifest, args.java_home, args.soot_cp, output, args.case)
    else:
        deps = (args.deps or ROOT / "dependencies" / args.framework).resolve()
        if args.action == "fetch":
            result = fetch(args.framework, deps)
        else:
            result = runtime(args.framework, deps, args.java_home, output, args.case)
    save(output / "report.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
