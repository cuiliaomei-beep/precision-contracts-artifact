# Precision Contracts for Distributed Static Analysis

[Repository](https://github.com/cuiliaomei-beep/precision-contracts-artifact) · [Data downloads](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/tag/artifact-20261113)

This repository contains the implementation, example programs, inputs, and results used in the paper. It covers the finite semantic and placement experiments, the Dubbo and gRPC examples, HDFS fsync, the three Java points-to examples, Tomcat, and the Em3d worker experiment.

The result files supplied here are the ones used for the paper. Reproduction commands write new output beneath runs. The implementation and data for other research directions are outside this repository.

## Getting started

Use Python 3.10 or later. The Python examples run on Linux and Windows. Soufflé and the C++ reference build use Linux, including WSL2.

```sh
python -m pip install -r requirements.txt
python scripts/finite.py verify --limit 3 --jobs 2
python scripts/points_to.py check
python scripts/em3d.py check
python scripts/rpc.py check
python scripts/hdfs.py check
python scripts/tomcat.py summary
```

These commands check a small selection of finite inputs and the supplied real-program results. The complete commands and their scope are described below.

## Paper experiments

| Paper part | Input and result | Reproduction guide |
|---|---|---|
| RQ1 | 450 finite programs, 308,895 schedules, 295 exact results and 155 conservative results | [Finite models](docs/finite.md) |
| RQ1 address information | 1,044 address variants of 155 programs and four address descriptions of the 450 inputs | [Finite models](docs/finite.md#address-information) |
| RQ2 | 400 programs and 160,000 complete placement and delivery results | [Finite models](docs/finite.md#new-placement-executions) |
| RQ3 RPC | Eight Dubbo/gRPC examples and 368 complete analysis states | [RPC examples](docs/rpc.md) |
| RQ3 HDFS | Eleven source methods, 520 source-model comparisons, and 16 runtime observations | [HDFS fsync](docs/hdfs.md) |
| RQ3 Java heap analysis | Complete Spark and Soufflé relations for Em3d, BH, and MST | [Points-to examples](docs/points-to.md) |
| RQ3 Tomcat | 71 relations, 11,618,755 tuples, one-thread and four-thread results | [Tomcat](docs/tomcat.md) |
| RQ4 | Eighteen Em3d process executions over eight distinct owner maps | [Em3d workers](docs/em3d.md) |

The finite placement experiment uses a worker simulator. The Em3d experiment starts separate processes on one host and sends actual TCP messages. Tomcat uses one process with different OpenMP thread counts. Each comparison retains the same analysis model within its own experiment.

## Complete result checks

```sh
python scripts/finite.py verify --jobs 4
python scripts/finite.py addresses --jobs 4
python scripts/rpc.py check
python scripts/hdfs.py check
python scripts/points_to.py check
python scripts/em3d.py check
```

The finite verifier recomputes the semantic observations and reads every saved placement state. It checks the original equations and complete node values. RPC and points-to comparisons also use complete states. The HDFS command checks the finite source model against the supplied source inputs and runtime observations.

The two full Tomcat outputs are attached to the [artifact-20261113 release](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/tag/artifact-20261113). Each compressed archive is about 137 MiB. GitHub CLI can obtain these assets using the account that has access to the repository.

```sh
python scripts/tomcat.py fetch
python scripts/tomcat.py verify
```

The verifier reads every relation and compares the actual tuples across the two outputs. It also checks each relation's arity, ordering, tuple count, and SHA-256.

## New executions

```sh
python scripts/finite.py semantic --jobs 4
python scripts/finite.py layout --limit 1 --jobs 1
python scripts/em3d.py run
python scripts/rpc.py analyze --quick
```

Omit the finite layout limit to produce the complete 160,000-run matrix. The RPC quick option reduces only the number of worker configurations. Each guide explains the commands for a complete execution, Java dependencies, source extraction, and larger Tomcat runs.

## Tables

```sh
python paper/scripts/gen_assets.py
```

The script reads paper/evidence/results.json and generates the five data tables and number macros. This small snapshot identifies its source records. Experiment results and complete input programs remain in the experiment directories.

The file manifest can be checked with python scripts/check_files.py. Reports from the artifact preparation are in docs/finite-validation.json, validation/, and verification/rpc_hdfs/.

## Files

| Directory | Contents |
|---|---|
| artifact/ | Event semantics, reference solvers, worker engines, and example generation |
| scripts/ | Portable reproduction commands |
| experiments/ | Paper inputs, original result records, frontend sources, and verification scripts |
| paper/ | Table generation and its result summary |
| metadata/ | File sources and checksums |
| docs/ | Experiment-specific instructions and validation records |
| release-assets/ | Downloaded or locally prepared Tomcat archives |
| runs/ | New outputs produced by reproduction commands |

Dependencies and downloaded benchmark sources have their own upstream terms. Source locations and versions are recorded in the experiment guides. The one-sided memory example in the paper is explained by the finite event model. The runtime measurements in this repository retain the execution scopes described above.
