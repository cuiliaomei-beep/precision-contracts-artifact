"""Create the two complete Tomcat result archives from existing sorted relations."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def package(source, destination, case, threads, relations):
    if destination.exists():
        raise FileExistsError(destination)
    partial = destination.with_suffix(destination.suffix + ".part")
    metadata = json.dumps({"run": case, "threads": threads}, sort_keys=True).encode("utf-8")
    with partial.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=1, mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                info = tarfile.TarInfo("run.json")
                info.size = len(metadata)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(metadata))
                for row in relations:
                    path = source / (row["relation"] + ".sorted.tsv")
                    if path.stat().st_size != row["bytes"]:
                        raise ValueError(f"Unexpected source size: {path}")
                    info = tarfile.TarInfo("canonical/" + row["relation"] + ".tsv")
                    info.size = path.stat().st_size
                    info.mode = 0o644
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
    partial.replace(destination)
    return {
        "file": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
        "run": case,
        "threads": threads,
        "relations": len(relations),
        "tuples": sum(row["tuples"] for row in relations),
        "uncompressed_relation_bytes": sum(row["bytes"] for row in relations),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing phase_w1/ and phase_w4/")
    parser.add_argument("--destination", type=Path, default=ROOT / "release-assets")
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    reference = json.loads((HERE / "results/reference_relations.json").read_text(encoding="utf-8"))
    rows = []
    for case, threads in (("phase_w1", 1), ("phase_w4", 4)):
        print(f"Packaging {case}", flush=True)
        rows.append(package(
            args.source / case,
            args.destination / f"tomcat-{case}-relations.tar.gz",
            case, threads, reference["relations"],
        ))
        print(json.dumps(rows[-1]), flush=True)
    manifest = {
        "repository": "cuiliaomei-beep/precision-contracts-artifact",
        "release_tag": "artifact-20261113",
        "assets": rows,
    }
    (HERE / "assets.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
