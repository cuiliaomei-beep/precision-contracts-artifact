# Hadoop fsync source study

This component contains the Hadoop 3.3.6 `fsync` source slice, 11 extracted method bodies, finite heap inputs, a source-derived interpreter, ordinary exception-aware local liveness projection, and the 16 concrete MiniDFSCluster outcomes. It includes original and holder-check-removed variants. Removing that check is an experimental source edit, not a newly discovered production defect.

From the repository root:

```text
python scripts/hdfs.py check
python scripts/hdfs.py replay
```

`check` verifies upstream source hashes and saved runtime record hashes, checks all 520 rows against a separately implemented fixture specification, and compares the 16 concrete outcomes with source predictions. `replay` also runs the full and projected source interpreters afresh and compares their complete result objects, including query rows and traces, with the saved results. It writes new results and liveness/cost records under a new `runs/hdfs-...` directory. `--output` selects an unused directory.

Each variant has 8 runtime-derived inputs and 252 held-out request/heap combinations. All 520 rows agree. The two wrong-holder requests change from `LeaseExpiredException` to normal completion in the edited variant. The ordinary liveness baseline already preserves all rows and reduces logical local-environment cells. These counts exclude heap storage, caller stacks, wire traffic, and total process memory; they are not end-to-end speedups.

The interpreter covers an explicit finite heap and library/protocol contract, including the extracted dynamic object lookups, guards, exceptional branches and directory-loop backedges. It is a restricted source study rather than whole-HDFS static analysis. Snapshot semantics, other request protocols and unlisted library behavior remain outside that contract. The saved independent-verification report includes examples rejected as unknown.

## Repeating the real Java execution

Use Linux or WSL, JDK 11, and Hadoop 3.3.6 revision `1be78238728da9266a4f88195058f08fd012bf9c`. `hdfs_runtime/dependencies.json` lists every original runtime dependency and two source jars, with version, source URL and SHA-256. The source jars are required to rebuild the holder-check variant. No Hadoop binaries are bundled.

```text
python scripts/hdfs.py fetch
python scripts/hdfs.py runtime --hadoop-home dependencies/hdfs --java-home PATH_TO_JDK11
```

The fetch command assembles the Java classpath layout and extra Hadoop test-utility dependencies using pinned Maven artifacts. A full Hadoop distribution is optional; the harness needs its listed Java jars. To use an existing installation and test jars, provide `--hadoop-home PATH --deps PATH_TO_EXTRA_JARS`. Environment variables `HADOOP_HOME` and `JAVA_HOME` are also accepted. The runtime entry point checks all recorded dependency hashes, copies the execution harness into a fresh run directory, compiles the fixture, and runs both variants. The original and edited NameNode/DataNode cluster execute with a separate client JVM and two DFSClient objects. Each variant also has a server-absent network check. Mockito is needed by Hadoop's test utility classes; the supplied fixture uses no mocks or spies.

The Java execution was repeated with the original pinned jars under WSL during packaging. The Maven fetch recipe was also checked against all 151 recorded jar hashes. This assembles the Java dependencies rather than a full Hadoop distribution.

## Repeating source extraction

Use the Soot dependency recipe in `docs/rpc.md`, then:

```text
python scripts/hdfs.py export --hadoop-home PATH_TO_HADOOP --java-home PATH_TO_JDK11 --soot-cp "PATH_TO_EXPORTER_JARS/*"
```

For the edited variant add `--variant mutant --mutation-classes runs/RUNTIME_RUN/harness/mutation/classes`. The method list is `novelty_audit/methods.json`; the exporter writes its configuration, log and extracted JSON under the new run directory. The supplied source files retain their Apache license headers. Historical data contains the original machine's paths as provenance, while new execution uses the explicit arguments above.
