"""Check recorded Em3d traces or run the same solver in new worker processes."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/em3d_next_merge_20261110"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "check":
        output = args.output or ROOT / "runs/record-checks/em3d.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            sys.executable, "-B", str(EXPERIMENT / "verify.py"), str(EXPERIMENT),
            "--output", str(output),
        ], check=True)
        print(output)
        return
    output = args.output or ROOT / "runs/em3d"
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    shutil.copytree(EXPERIMENT / "souffle_fresh", output / "souffle_fresh")
    subprocess.run([
        sys.executable, "-B", str(EXPERIMENT / "run_baseline.py"), "--out", str(output / "runs"),
    ], check=True)
    subprocess.run([
        sys.executable, "-B", str(EXPERIMENT / "verify.py"), str(output),
        "--output", str(output / "VERIFICATION.json"),
    ], check=True)
    subprocess.run([sys.executable, "-B", str(EXPERIMENT / "summarize.py"), str(output)], check=True)


if __name__ == "__main__":
    main()
