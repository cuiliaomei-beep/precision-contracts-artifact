# Finite semantic and placement experiments

This part reproduces RQ1 and RQ2. It contains the event-language implementation, the complete input programs, the concrete schedule enumerator, the abstract solver, and the recorded node states. It also includes the address-information variants used in RQ1.

Run commands from the repository root with Python 3.10 or later. The implementation uses NumPy and psutil.

```sh
python -m pip install numpy psutil
python scripts/finite.py verify --limit 3 --jobs 2
```

The small verification checks three semantic programs, all 400 placement and delivery combinations for each of three layout programs, and three address-envelope cases. The address cases come from the programs with additional abstract observations, so a small selection still exercises address variation.

## Complete verification

```sh
python scripts/finite.py verify --jobs 4
```

This command recomputes the concrete and abstract results of all 450 semantic programs. It compares every variable in the saved abstract state and every designated observation. It then reads all 160,000 recorded layout states, compares each complete state with a newly computed centralized result, checks the original equations, and checks the owner mapping and message totals. Finally, it re-executes the 1,044 address variants of the 155 conservative programs and compares their complete observation envelope with the saved abstract result.

The expected semantic result is 295 exact and 155 conservative programs, with no missing concrete observation. Exactness here means equality of the scalar-value set and the may-taint projection. It does not compare the relation between an individual value and its taint history. The concrete enumerator counts 308,895 complete legal schedules. The address envelope covers 2,553,288 complete schedules across its different address choices.

Every command writes a new directory beneath `runs/` and prints its location. The supplied program and result files are read as inputs. The command returns a nonzero exit status when a comparison fails or an execution is incomplete. `--out runs/my-run` chooses a result directory, which must not already exist.

## New semantic executions

```sh
python scripts/finite.py semantic --limit 5 --jobs 2
python scripts/finite.py semantic --jobs 4
```

The first command is a small example. The second runs all 450 semantic inputs and saves their complete concrete observations, abstract observations, and abstract node states.

## New placement executions

```sh
python scripts/finite.py layout --limit 1 --jobs 1
python scripts/finite.py layout --jobs 4
```

For each selected program, the command executes five placement policies, worker counts 1, 2, 4, and 8, and delivery seeds 42 through 61. `--limit` counts programs. One program therefore produces 400 executions, and the full command produces 160,000 executions over 400 programs. The output contains complete states in one compressed JSONL file per program.

Workers in this experiment are discrete-event simulator objects. The operating-system processes selected by `--jobs` execute different programs. These runs test result equality under different layouts and message deliveries. The reported worker counts and times do not measure a multi-machine speedup.

## Address information

```sh
python scripts/finite.py addresses --limit 3 --jobs 2
python scripts/finite.py addresses --jobs 4
```

The full command solves the same 450 programs under four static address descriptions. It expects 295 exact programs with the supplied descriptions, 346 with precise read targets, 295 with precise write targets, and 450 with both targets precise. The extra target information changes the static metadata while retaining the concrete region accesses. The command also repeats the complete address-envelope check. It does not infer the extra information automatically.

The finite RMA model uses sequentially consistent access events. A local completion event permits later use of a read result. Cross-component ordering is supplied explicitly. These semantics are documented by the input programs and their operation handlers. They do not represent every MPI memory or synchronization rule.

## File map

| Location | Contents |
|---|---|
| `artifact/core/` | Event language and concrete operation semantics |
| `artifact/oracle/` | Complete concrete state exploration and schedule counting |
| `artifact/analysis/` | Abstract domains, semantic dependencies, and centralized equations |
| `artifact/distributed/` | Simulated worker queues and serialized updates |
| `artifact/partition/` | The five placement policies |
| `artifact/instantiations/` | RPC and remote-memory examples |
| `artifact/generator/` | Finite program generator |
| `artifact/experiments/rq1.py` and `rq2.py` | Existing experiment logic used by the portable entry point |
| `experiments/legacy_repair_20261016/corpus/` | All 850 complete program inputs and indexes |
| `experiments/legacy_repair_20261016/rq1/programs/` | The 450 semantic results |
| `experiments/legacy_repair_20261016/rq2/programs/` | Per-program centralized states and run summaries |
| `experiments/legacy_repair_20261016/rq2/runs/` | All 160,000 complete states in 400 compressed files |
| `experiments/precision_audit_20261016/cases/` | The 155 additional-observation cases and their address variants |
| `experiments/precision_audit_20261016/ALIAS_ORACLE_AUDIT.json` | Complete address-envelope results |
| `docs/finite-sources.json` | Original file sizes and SHA-256 values |

The supplied JSON retains the field names used when the experiments were run. The portable entry point interprets relative program and result paths inside this repository; it does not require the original workstation paths recorded in older metadata.

The implementation's semantic and message checks can also be run directly.

```sh
python -m unittest discover -s artifact/tests -v
```
