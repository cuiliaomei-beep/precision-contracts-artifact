"""Check the sizes and SHA-256 values listed in the repository manifest."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "metadata/files.json").read_text(encoding="utf-8"))
    failed = []
    for row in manifest["files"]:
        path = ROOT / row["path"]
        if not path.is_file():
            failed.append(row["path"])
            continue
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                size += len(block)
        if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
            failed.append(row["path"])
    if failed:
        raise ValueError("Files differ from the manifest: " + ", ".join(failed))
    print(f"PASS: {len(manifest['files'])} repository files")


if __name__ == "__main__":
    main()
