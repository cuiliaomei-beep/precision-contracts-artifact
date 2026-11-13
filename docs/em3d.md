# Em3d worker execution

The paper follows the store and load of the Node.next field through actual worker processes. The adapter uses the same dynamic-edge installation, existing-value replay, and incremental propagation as the owner-computes solver.

## Supplied runs

The experiment directory includes 18 traces, their final memberships, per-exchange states, source tags, and message dispositions. It also includes the nine placement labels, eight distinct owner maps, independent Soufflé outputs, and four C++ reference runs.

```sh
python scripts/em3d.py check
```

The checker compares every final fact with both reference relations, checks every intermediate state against the original rules, and verifies state growth. Its report is written beneath runs/record-checks.

## New execution

```sh
python scripts/em3d.py run
```

This starts separate processes and uses TCP loopback. One schedule exchanges each frontier. The other drains local work before exchanging remote events. Every run begins from the input allocation seeds. New traces and verification results are written under runs/em3d.

Each final result has 401 variable and 38 heap memberships. The complete solve checks 502 candidate facts and installs 98 dynamic edges. Direct next propagation installs four edges and checks four candidates, producing three new memberships and one duplicate. The single-worker execution has the same candidate count.

The processes share one physical host. Intermediate states can have outstanding remote obligations even when each local queue is empty. These traces measure ongoing distributed propagation. The data support the small repair cost reported for this example.

The C++ implementation is in experiments/precision_partition_20261017/classic_owner.cpp. It can be built on Linux with g++ -O3 -std=c++17 -fopenmp. That implementation uses OpenMP within one process. Its supplied runs provide a separate complete-result comparison.
