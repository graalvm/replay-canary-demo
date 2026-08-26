# Replay Canary

The replay canary is a command-line tool for detecting changes in GraalVM
compiler performance using realistic, deterministic compilation workloads. It
records the hottest compilations from benchmark suites, replays the same
compilation inputs with different compiler builds, and produces detailed
comparison reports.

As an inexpensive screening and diagnostic signal, the tool complements
ordinary benchmarks. It detects compiler changes and provides deterministic
reproducers, while full benchmark runs remain necessary to measure the runtime
performance of generated code.

We present the approach in an accompanying conference paper. This repository
contains the **reusable replay canary tool**, which implements the complete
workflow. Each step can be invoked with a separate command. The tool can run
locally or be integrated into another continuous integration (CI) pipeline.
The production deployment evaluated in the paper uses a variant of the tool
that integrates with organization-specific internal services.

## How it works

The replay canary separates the workflow into four explicit steps:

1. `build` checks out the compiler version to test, obtains the required
   builder JDK, and builds the compiler and replay launcher dependencies.
2. `record` profiles benchmark workloads and creates a corpus containing replay
   files for their hottest compilations.
3. `replay` runs every compilation in a corpus with a selected compiler build
   and stores the measured results.
4. `compare` compares two replays of the same corpus and generates an HTML
   report and a Markdown summary.

```text
     version A                      version B                      version C
         │                              │                              │
       build                          build                          build
         │                              │                              │
         ▼                              ▼                              ▼
      build A                        build B                        build C
         │                              │                              │
       record                           │                              │
         │                              │                              │
         ▼                              │                              │
       corpus                           │                              │
         │                              │                              │
         ├──────────────────────────────┼──────────────────────────────┤
         │                              │                              │
       replay                         replay                         replay
         │                              │                              │
         ▼                              ▼                              ▼
     replay A                       replay B                       replay C
         │                             │ │                             │
         │                             │ │                             │
         └──────────────┬──────────────┘ └──────────────┬──────────────┘
                        │                               │
                     compare                         compare
                        │                               │
                        ▼                               ▼
                    report A/B                      report B/C
```

A replay is not inherently a baseline or a candidate. Those roles are selected
when a comparison is created, so the same replay can be reused in multiple
comparisons. The replay canary verifies that the selected baseline and candidate
were produced from the exact same corpus.

Replay compilation is described in the
[GraalVM compiler documentation](https://github.com/oracle/graal/blob/master/compiler/docs/ReplayCompilation.md).

## Reported metrics

The replay canary collects metrics for each replay iteration and each
compilation:

- **Retired instructions** measure the instructions executed by the compiler
  and provide a stable proxy for compiler work.
- **Allocated memory** measures bytes allocated on the Java heap during
  compilation.
- **Target-code size** measures the size of the generated machine code.
- **Target-code hash** identifies whether the generated machine code changed.
- **Wall-clock and thread time** provide additional diagnostic measurements.
- **Compiled bytecodes** provide context for the amount of input processed.

Iteration zero is warmup and is excluded from run metrics. For every numeric
metric, the value of a run is the arithmetic mean of its remaining replay
iterations:

```text
run value = mean(measured replay iterations)
```

### HTML report

The HTML report shows individual iterations, runs, corpus totals, and their
relative changes. Corpus totals use all comparable runs:

```text
baseline corpus total  = sum(all baseline run values)
candidate corpus total = sum(all candidate run values)
corpus ratio           = candidate corpus total / baseline corpus total
```

Compilation rows match records by iteration and compile ID and show their
metric and target-code differences.

### Markdown summary

The Markdown summary reports relative changes per workload:

```text
run ratio      = candidate run value / baseline run value
workload ratio = mean(run ratios for that workload)
```

A metric is reported when its workload ratio differs from `1` by more than the
configured threshold. The summary also reports changed target-code hashes and
the numbers of failed and skipped runs. It is saved as `summary.md` and printed
to standard output.

By default, retired instructions use a 3% change threshold, while allocated
memory and target-code size use 2% thresholds. These defaults can be overridden
under `[compare]`. Wall-clock time, thread time, and compiled bytecodes are
reported as diagnostic metrics without change thresholds.

## Requirements

- x86-64 Linux
- Python 3.10 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A checkout of the [Graal repository](https://github.com/oracle/graal)
- An available [`mx`](https://github.com/graalvm/mx) executable
- [PAPI](https://github.com/icl-utk-edu/papi) for retired-instruction
  measurements

The GraalVM compiler documentation contains the current
[PAPI setup instructions](https://github.com/oracle/graal/blob/master/compiler/docs/ReplayCompilation.md#performance-counters).

## Installation

Clone replay canary and install its command and dependencies:

```bash
uv sync
uv run replay-canary --help
```

## Configuration

Copy the example configuration and set the path to the Graal checkout:

```bash
cp replay-canary.example.toml replay-canary.toml
```

```toml
[tools]
mx = "mx"

[graal]
repository = "../graal"

[data]
directory = "replay-canary-data"

[record]
benchmark_suites = ["renaissance", "dacapo", "scala-dacapo"]
runs = 1
hot_window_size = 30
hot_method_threshold = "0.001"

[replay]
iterations = 2
retired_instruction_event = "PAPI_TOT_INS"

[compare]
retired_instructions_threshold = "0.03"
allocated_memory_threshold = "0.02"
target_code_size_threshold = "0.02"
```

`replay-canary.toml` contains local paths and experiment defaults and is ignored
by Git.
Relative paths are resolved from the directory containing the configuration
file. Use `--config PATH` to select a different configuration file.

The recording settings select the benchmark suites included in a corpus, the
number of recording runs per workload, and the rolling hot-method policy.
Use the global `--graal-repository PATH` option to override `[graal].repository`
for one command. The replay iteration count includes the warmup iteration.
`replay.retired_instruction_event` selects the PAPI event interpreted as the
retired-instruction count. The default event is `PAPI_TOT_INS`.
Comparison thresholds are relative fractions, so `0.03` means 3%. They
determine which metric changes appear in the comparison summary but do not
affect the command's exit status.

The replay canary looks for configuration in this order:

1. the path supplied with `--config`;
2. `./replay-canary.toml`; and
3. built-in defaults.

Command-line options override values loaded from the configuration file.

## Local data

The replay canary stores its local state in the directory configured by
`data.directory`, which defaults to `./replay-canary-data/` in the example
configuration. The default local directory is ignored by Git. Use the global
`--data-dir` option for a one-off override.

```text
replay-canary-data/
├── hot-method-windows/
├── corpora/
├── replays/
├── comparisons/
└── work/
```

- `hot-method-windows/` contains profiling history used to choose methods
  for subsequent recordings.
- `corpora/` contains replay files and recording metadata.
- `replays/` contains measurements produced by replaying a corpus
  with a compiler build.
- `comparisons/` contains comparison metadata, HTML reports, and Markdown
  summaries.
- `work/` contains disposable intermediate files.

Corpora, replays, and comparisons receive generated identifiers. The
optional `--label` argument gives them memorable names for use in later
commands. Labels are unique within their artifact type; stored relationships
use identifiers rather than labels.

Every corpus and replay selector accepts either a generated identifier or a
label.

## Build a compiler version

Check out a GraalVM compiler revision and build libgraal and the dependencies
required by the replay launcher:

```bash
uv run replay-canary build --revision master
```

`build` is a convenience wrapper. It leaves the resulting GraalVM in the
configured Graal repository rather than copying or registering it in the data
directory. A later build in the same worktree may overwrite these artifacts.
Builder JDKs and incremental build outputs remain in their normal `mx`
locations.

The compiler revision defaults to `HEAD`. The build command reuses the LabsJDK
selected for the checked-out revision when it is already installed. Otherwise,
`mx` fetches it. In either case, `mx` creates or repairs its standard
`labsjdk-ce-latest` symlink before the compiler build proceeds:

1. checks out the selected compiler version;
2. runs
   `mx -y fetch-jdk --skip-digest-check -A labsjdk-ce-latest`;
3. uses the `labsjdk-ce-latest` Java home resolved by `mx`;
4. builds libgraal, the replay launcher dependencies, and the PAPI bridge; and
5. prints the resulting GraalVM home.

`record` and `replay` locate the existing GraalVM build for the selected
checkout with `mx -p <graal-repository>/vm --env libgraal graalvm-home`. They
assume that the build belongs to the checked-out revision. To record or replay
with a build in a separate worktree, pass the global `--graal-repository PATH`
option before the `record` or `replay` subcommand.

## Record a corpus

After building the selected compiler version, record the hottest compilations
from the configured benchmark suites:

```bash
uv run replay-canary \
  record \
  --label renaissance-corpus
```

Recording profiles each workload, updates its hot-method window, and writes a
new corpus. Existing corpora are never modified.

The package provides bootstrap profiles for DaCapo, Scala DaCapo, and
Renaissance to seed hot-method windows that do not yet have local history.

For a quicker end-to-end test, restrict the corpus to one workload:

```bash
uv run replay-canary \
  record \
  --workload renaissance:scrabble \
  --label scrabble-smoke-test
```

`--workload SUITE:WORKLOAD` may be repeated. When present, it overrides the
configured benchmark-suite selection and records only the listed workloads.
For temporary experiments, `--benchmark-suite`, `--runs`,
`--hot-window-size`, and `--hot-method-threshold` override their corresponding
TOML defaults.

Pass additional arguments to recorded benchmark JVMs with repeatable
`--jvm-arg` options:

```bash
uv run replay-canary record \
  --workload renaissance:scrabble \
  --jvm-arg=-Djdk.graal.PrintCompilation=true
```

## Replay a corpus

After building the selected compiler version, replay the corpus once with the
baseline compiler:

```bash
uv run replay-canary \
  replay \
  --corpus renaissance-corpus \
  --label main-before-change
```

Check out a Graal revision under test and build it:

```bash
uv run replay-canary build --revision my-change
```

Then replay the same corpus:

```bash
uv run replay-canary \
  replay \
  --corpus renaissance-corpus \
  --label candidate-my-change
```

Each replay records the corpus identifier, compiler version and build
metadata, additional replay arguments, execution status, and measured results.

Use `--iterations` to override `replay.iterations` for one invocation.

Pass an additional JVM argument to `mx replaycomp` with repeatable
`--replay-arg` options. Values must start with `-D` or `-X`; use the `=` form so
the command-line parser does not interpret the value as another option:

```bash
uv run replay-canary replay \
  --corpus renaissance-corpus \
  --label no-floating-reads \
  --replay-arg=-Djdk.graal.OptFloatingReads=false
```

The replay canary fixes the libgraal heap at 12 GiB, requests the same size for
its young generation, and delays hinted garbage collection until Eden is full.
The replay harness also requests garbage collection before each iteration.
Together, these measures reduce garbage-collection noise during measured replay
iterations.

## Compare two replays

Compare the saved replay measurements:

```bash
uv run replay-canary \
  compare \
  --baseline main-before-change \
  --candidate candidate-my-change \
  --label my-change-vs-main
```

On success, `compare` writes `manifest.json`, `summary.md`, and a standalone
`report.html` under the configured comparison data directory. The command fails
if the baseline and candidate replays reference different corpora.

## Reproducing an individual compilation

The comparison report identifies the corpus run and replay file for every
compilation. A replay file or directory can also be passed directly to
`mx replaycomp`:

```bash
mx -p <graal-repository>/compiler replaycomp \
  replay-canary-data/corpora/<corpus-id>/<run>/<compilation-id>.replay
```

This provides a deterministic reproducer for debugging compiler behavior. See
the [GraalVM replay-compilation documentation](https://github.com/oracle/graal/blob/master/compiler/docs/ReplayCompilation.md)
for instructions on passing diagnostic options and attaching a Java debugger.

## Troubleshooting

Retired-instruction measurements require working PAPI. On CPUs that libpfm does
not recognize, forcing a PMU model usually works because retired instructions
is an architectural event. Set the `LIBPFM_FORCE_PMU` environment variable and
verify the `PAPI_TOT_INS` count reported by PAPI:

```bash
export LIBPFM_FORCE_PMU=amd64
papi_command_line PAPI_TOT_INS
```

If `record` or `replay` cannot find a GraalVM, run `replay-canary build` for the
selected compiler revision and worktree. The build and checkout must match so
that the replay launcher and compiler agree.

If an incremental build fails because of stale build outputs, clean the
generated artifacts before retrying:

```bash
mx -p <graal-repository>/vm --env libgraal clean --all
mx -p <graal-repository>/compiler clean --all
```

If `mx` reports that it is too old, check out the required version tag in the
`mx` repository, then rerun the build.

A newly published corpus may contain no replay files when its hot-method
windows do not yet contain enough history. This is valid: successful profiling
advances the separately stored windows for later recordings. Record the
workload again to create a corpus from the updated window.

Failed commands retain their invocation directory under
`replay-canary-data/work/` and include that path in the error message. The tool
does not delete workspaces for failed recording runs. Successful recording runs
remove their temporary files as they finish. Fully successful commands remove
their invocation workspace after atomically publishing their corpus, replay,
or comparison.

## Development

Install all dependencies:

```bash
uv sync
```

Format the source:

```bash
uv run isort .
uv run black .
```

Run type checks and tests:

```bash
uv run mypy .
uv run pytest .
```

## License

Copyright (c) 2026, Oracle and/or its affiliates. All rights reserved.

The replay canary is licensed under the Universal Permissive License (UPL),
Version 1.0.
See [LICENSE](LICENSE) for the complete license text.
