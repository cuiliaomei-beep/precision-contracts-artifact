[Repository](https://github.com/cuiliaomei-beep/precision-contracts-artifact) · [Release downloads](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/tag/artifact-20261113)

The release contains both complete Tomcat relation outputs used for the one-thread and four-thread comparison.

Each archive contains 71 sorted relation files, 11,618,755 tuples, and 2,076,206,212 bytes of relation data. Each compressed archive is 143,315,832 bytes.

The repository includes the input archive, analysis rules, complete relation counts and hashes, and scripts for checking and reproducing the results. Download and verify the assets with these commands.

```sh
python scripts/tomcat.py fetch
python scripts/tomcat.py verify
```

The gRPC runtime archive contains the eight 1.66.0-SNAPSHOT jars used by the Java examples, together with their upstream license and notice. The RPC download command obtains this archive and the public Maven dependencies, checking their SHA-256 values before use.

```sh
python scripts/rpc.py fetch --framework grpc
```
