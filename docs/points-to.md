# Java points-to examples

The paper uses Em3d, BH, and MST from JOlden. Their variable and heap memberships are compared under the same allocation sites, fields, and destination type masks.

The repository contains each exported program graph, the Spark memberships, five input relations for Soufflé, and complete Soufflé outputs. The original four-rule Python solver is included as a second evaluator.

## Result checks

From the repository root, run the following command.

```sh
python scripts/points_to.py check
```

This computes a new Python solution and compares every variable and heap membership with Spark and the supplied Soufflé results. The expected totals are 439 for Em3d, 1,391 for BH, and 364 for MST.

With Soufflé 2.5 on PATH, run the same rules independently.

```sh
python scripts/points_to.py check --engine both --output runs/points-to-souffle
```

Outputs go beneath runs. Use a different output directory for another execution with Soufflé.

## Source and frontend

JOlden sources are available at commit b0ea8356a88702f5d3549b871aae8fdbcba6db7c of https://github.com/reprogrammer/jolden. The input manifest records each source URL and SHA-256. The download command obtains the Java files and checks those hashes.

```sh
python scripts/points_to.py fetch-sources
```

Source extraction needs Java 17, Java 8's rt.jar, Soot 4.5.0, and Gson 2.10.1. The runtime library and dependency jars are supplied through command arguments.

Maven can obtain the Soot dependencies using the included POM. Run the command from the repository root and replace the output path with an absolute directory on your machine.

```sh
mvn -f tools/soot-pom.xml dependency:copy-dependencies -DoutputDirectory=/absolute/path/to/deps/soot
```

```sh
python scripts/points_to.py export em3d \
  --java8-rt /path/to/java8/jre/lib/rt.jar \
  --classpath '/path/to/soot-and-gson/*' \
  --output runs/export-em3d
```

The exporter compiles the application for Java 8 and excludes JDK bodies. This reproduces the application-only constraint model used in the paper. Its agreement checks concern that model. Reflection, native methods, and other excluded library behavior require further modeling.
