# Precision Contracts for Distributed Static Analysis

[Repository](https://github.com/cuiliaomei-beep/precision-contracts-artifact) · [Data downloads](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/tag/artifact-20261113)

Implementation, example programs, analysis inputs, and experimental results for distributed static analysis. The repository covers finite semantic and placement experiments, Dubbo and gRPC examples, HDFS fsync, Java points-to analysis, Tomcat, and Em3d worker execution.

Source and data downloads are public and work without signing in. New executions write beneath `runs/`; supplied inputs and results remain available for comparison.

## Getting started

Install Git and Python 3.10 or later. Clone the repository and enter its root directory:

```sh
git clone https://github.com/cuiliaomei-beep/precision-contracts-artifact.git
cd precision-contracts-artifact
```

On Linux or macOS, create and activate a Python environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, use the environment's Python directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For the following commands, Windows users can replace `python` with `.\.venv\Scripts\python.exe`. Run commands from the repository root.

```sh
python scripts/check_files.py
python scripts/finite.py verify --limit 3 --jobs 2
python scripts/points_to.py check
python scripts/em3d.py check
python scripts/rpc.py check
python scripts/hdfs.py check
python scripts/tomcat.py summary
```

These checks need only Python and the packages in `requirements.txt`. They verify a small finite-input selection and the supplied real-program results. A successful command exits with status zero. Commands print results or the location of a detailed report under `runs/`. Java, Maven, and Souffle are needed only for the additional executions described in the experiment guides.

## Experiments

| Experiment | Input and result | Reproduction guide |
|---|---|---|
| Finite semantics | 450 programs, 308,895 schedules, 295 exact results and 155 conservative results | [Finite models](docs/finite.md) |
| Address information | 1,044 address variants of 155 programs and four address descriptions of the 450 inputs | [Address information](docs/finite.md#address-information) |
| Placement and delivery | 400 programs and 160,000 complete saved states | [Placement executions](docs/finite.md#new-placement-executions) |
| RPC | Eight Dubbo/gRPC examples and 368 complete analysis states | [RPC examples](docs/rpc.md) |
| HDFS fsync | Eleven source methods, 520 source-model comparisons, and 16 runtime observations | [HDFS fsync](docs/hdfs.md) |
| Java heap analysis | Complete Spark and Souffle relations for Em3d, BH, and MST | [Points-to examples](docs/points-to.md) |
| Tomcat | 71 relations and 11,618,755 tuples in each of the one-thread and four-thread results | [Tomcat](docs/tomcat.md) |
| Em3d workers | Eighteen process executions over eight distinct owner maps | [Em3d workers](docs/em3d.md) |

The finite placement experiment uses a worker simulator. Em3d starts separate processes on one host and sends TCP messages. Tomcat uses one process with different OpenMP thread counts. Each comparison retains the same analysis model within its experiment.

## Complete result checks

```sh
python scripts/finite.py verify --jobs 4
python scripts/finite.py addresses --jobs 4
python scripts/rpc.py check
python scripts/hdfs.py check
python scripts/points_to.py check
python scripts/em3d.py check
```

The finite verifier recomputes semantic observations and reads every saved placement state. It checks the original equations and complete node values. RPC and points-to comparisons also use complete states. The HDFS command checks its finite source model against the supplied source inputs and runtime observations.

## Downloading complete results and dependencies

The [release](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/tag/artifact-20261113) provides these files:

| File | Size | Contents |
|---|---|---|
| [tomcat-phase_w1-relations.tar.gz](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/download/artifact-20261113/tomcat-phase_w1-relations.tar.gz) | 143,315,832 bytes | Complete one-thread Tomcat result |
| [tomcat-phase_w4-relations.tar.gz](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/download/artifact-20261113/tomcat-phase_w4-relations.tar.gz) | 143,315,832 bytes | Complete four-thread Tomcat result |
| [grpc-runtime-1.66.0-snapshot.zip](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/download/artifact-20261113/grpc-runtime-1.66.0-snapshot.zip) | 10,251,845 bytes | Eight original gRPC runtime jars and their license and notice |

The commands below download over public HTTPS and check file sizes and SHA-256 values. GitHub CLI and a GitHub account are unnecessary.

```sh
python scripts/tomcat.py fetch
python scripts/tomcat.py verify
```

Tomcat verification streams both archives and compares every relation's actual tuples, arity, ordering, count, and hash. Each archive represents about 1.93 GiB of uncompressed relation data. Archive verification does not unpack both copies to disk; a new Tomcat analysis needs additional space for inputs, generated code, and output.

RPC runtime dependencies are obtained as follows; graph/state checks above do not require these downloads:

```sh
python scripts/rpc.py fetch --framework dubbo
python scripts/rpc.py fetch --framework grpc
```

The gRPC command downloads the exact snapshot archive and the remaining public Maven dependencies. Runtime execution additionally requires JDK 17 for Dubbo or JDK 11 for gRPC. See [RPC instructions](docs/rpc.md) for execution and bytecode extraction. HDFS dependency downloads and its Linux/WSL and JDK 11 requirements are described in [HDFS instructions](docs/hdfs.md).

## New executions

```sh
python scripts/finite.py semantic --jobs 4
python scripts/finite.py layout --limit 1 --jobs 1
python scripts/em3d.py run
python scripts/rpc.py analyze --quick
```

One finite layout program produces 400 executions. Omit `--limit 1` to run the complete 160,000-execution matrix. RPC's `--quick` option reduces the number of worker configurations. Each guide explains the full executions and their extra tools. Souffle and the C++ reference builds use Linux, including WSL2.

## Files

| Directory | Contents |
|---|---|
| `artifact/` | Event semantics, reference solvers, worker engines, example generation, and tests |
| `scripts/` | Reproduction and verification commands |
| `experiments/` | Inputs, example programs, frontend sources, original results, and experiment-specific scripts |
| `metadata/` | Source locations, dependency records, and file checksums |
| `docs/` | Experiment instructions and verification records |
| `tools/` | Soot dependency recipe |
| `validation/`, `verification/` | Recorded result checks and runtime observations |
| `release-assets/` | Downloaded Tomcat archives and gRPC runtime archive; created locally |
| `runs/` | New execution outputs; created locally |

`python scripts/check_files.py` checks the repository file manifest. Third-party sources and dependencies retain their upstream terms; their locations and versions are recorded in the experiment guides.
