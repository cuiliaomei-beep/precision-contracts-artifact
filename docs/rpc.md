# RPC provenance examples

This component contains four Dubbo and four gRPC cases: a response carrying the input, a clean sink, a constant reply, and two calls. It includes client/provider sources, protobuf sources and generated Java, deployment manifests, the Jimple exporter, exported graphs, sink expectations, provenance witnesses, and all 368 saved distributed states.

Run from the repository root with the Python requirements installed:

```text
python scripts/rpc.py check
python scripts/rpc.py analyze --quick
python scripts/rpc.py analyze
```

`check` recomputes an independent per-source reference, compares complete node states, and checks sink expectations and witnesses. `analyze` starts real Python worker processes and compares every node with the centralized result. The full run uses five placement heuristics, worker counts 1/2/4/8, seeds 0/1, and a placement that separates request and response edges whenever there are at least two workers: 46 configurations per case. `--quick` uses five configurations per case. `--case grpc/positive` selects one example. New output goes to a new `runs/rpc-...` directory; `--output` selects an unused directory.

The graph semantics are explicit string provenance under the recorded RPC and protobuf summaries. They do not include implicit flows or arbitrary transport/framework bodies. Successful replays establish complete-state agreement on these inputs, not a whole-Java soundness theorem or a speedup result.

## Repeating actual RPC execution

Dependency identities and SHA-256 values are in `experiments/e2e_20261016/dependencies.json`. Binary tools and jars are not included. For Dubbo:

```text
python scripts/rpc.py fetch --framework dubbo
python scripts/rpc.py runtime --framework dubbo --java-home PATH_TO_JDK17
```

For gRPC, the Release asset `grpc-runtime-1.66.0-snapshot.zip` contains exactly the eight original runtime jars plus upstream Apache license and notice files. The archive and each jar are checked against `dependencies.json`. The fetch command uses a local copy under `release-assets/` when available; otherwise it calls GitHub CLI to download the asset from this repository's `artifact-20261113` Release. A private repository requires the reader's own GitHub access. Public dependencies come from Maven Central.

```text
python scripts/rpc.py fetch --framework grpc
python scripts/rpc.py runtime --framework grpc --java-home PATH_TO_JDK11
```

The recorded gRPC runtime is **1.66.0-SNAPSHOT**, from commit `64ac792b5e6e57268b69abb6979ca0f389af1d9d` of `https://github.com/grpc/grpc-java`. It must not be replaced silently with released 1.66.0 jars. An optional source checkout is:

```text
git clone https://github.com/grpc/grpc-java
git -C grpc-java checkout 64ac792b5e6e57268b69abb6979ca0f389af1d9d
```

Inside that checkout, its Gradle wrapper and JDK 11 can build `:grpc-api:jar :grpc-context:jar :grpc-core:jar :grpc-util:jar :grpc-netty-shaded:shadowJar :grpc-protobuf:jar :grpc-protobuf-lite:jar :grpc-stub:jar` with tests skipped. Independent source builds are not assumed to reproduce the original jar archive metadata byte for byte; the supplied Release asset is the exact runtime dependency set for this study. The graph/state replay requires none of these jars. `--deps PATH` selects an existing dependency directory. Generated protobuf Java is included, so protoc is unnecessary for execution.

Both runtime commands compile the supplied sources, choose a loopback port, start provider and client as separate JVMs, check concrete sink values, then check that a client fails after the provider stops. All classes and logs go under the new run directory. The Dubbo upstream source is commit `0ef8eaeb534fdeb0158e07229b4cb01cb4b21918` of `https://github.com/apache/dubbo-samples`.

## Repeating bytecode extraction

Use Soot 4.5.0 and Gson 2.10.1 with their runtime dependencies. The included Maven file provides a dependency recipe:

```text
mvn -f experiments/e2e_20261016/frontend/pom.xml dependency:copy-dependencies -DoutputDirectory=PATH_TO_EXPORTER_JARS
python scripts/rpc.py export --manifest runs/RUNTIME_RUN/manifest.json --java-home PATH_TO_JDK17 --soot-cp "PATH_TO_EXPORTER_JARS/*"
```

The exporter reads the fresh runtime manifest rather than historical absolute paths. It checks signatures and bootstrap registration and rejects unsupported query operations. Feed an exported graph into the same distributed runner with the separately maintained expectation:

```text
python scripts/rpc_analysis.py --graph runs/EXPORT_RUN/positive.graph.json --expect experiments/e2e_20261016/results/grpc/positive/expected.json --output runs/rpc-positive-reexport
```

The dependency recipe pins direct versions; the original extraction environment's complete jar hashes remain in the saved provenance records. A new Maven dependency resolution is an explicit new extraction environment, not proof that every transitive jar is identical to that historical environment.

Upstream Apache source headers are retained. The `javax.annotation.Generated` stub is used only during compilation. Historical manifests retain their original paths for provenance; the entry points above do not dereference those paths.
