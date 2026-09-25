# arkcompiler-test

Black-box ArkCompiler fixture image for `abcd-rs`. The image contains ready-made
dynamic ABC files, original pandasm disassembly, source inputs and runtime oracles.
No OpenHarmony checkout, compiler toolchain, debug symbols, fuzz corpus, JIT/AOT
suite or `ark_stub_compiler` is included. The three upstream tools remain available
for investigation: `es2abc`, `ark_disasm`, `ark_js_vm`.

## Build from the existing OpenHarmony build

The OpenHarmony checkout and downloaded toolchain are local build inputs only.
Their names, paths and contents are ignored by Git. Docker's context is an allowlist;
the monorepo, toolchain and `.git` are not sent to Docker.

```sh
# Only if the upstream binaries need rebuilding:
cd <OpenHarmony-checkout>
python3 ark.py x64.release es2panda ark_disasm ark_js_vm -j8

cd <arkcompiler-test-checkout>
make build
make test
```

`scripts/prepare.py` requires the upstream revisions and selected file hashes in
`upstream.lock.json`. To intentionally update the upstream revision, review it and
run `python3 scripts/prepare.py --update-lock`, then commit the changed lock.
Preparation never rebuilds or changes OpenHarmony. It stages four binaries, selected
verbatim upstream inputs/expected outputs and third-party license notices.

The image uses Linux/amd64 Ubuntu 22.04 with `libstdc++6` (the tools require
GLIBCXX_3.4.30; a plain Ubuntu 20.04 runtime is insufficient). Docker first installs
runtime dependencies, then generates and validates the entire corpus inside the
image. The final stage contains the completed corpus. Generation fans the
(case × version × profile) jobs out to a worker pool (`ARK_TEST_JOBS` overrides
the worker count; the default is all cores) and the resulting corpus is
byte-identical to a serial run. Compilation or oracle failure fails the build —
all failures are collected and reported; no failed fixtures are silently skipped.
Building may need network access for the base image and apt; using the finished
image does not. `prepare.py` also downloads the pinned test262 tarball (network
at prepare time only).

## Version contract

| ABC version | Compiler parameters |
|---|---|
| `9.0.0.0` | `--target-api-version=9` |
| `11.0.2.0` | `--target-api-version=11` |
| `12.0.2.0` | `--target-api-version=12 --target-api-sub-version=beta1` |
| `12.0.6.0` | `--target-api-version=12 --target-api-sub-version=beta3` |
| `13.0.1.0` | `--target-api-version=18` |
| `24.0.0.0` | `--target-api-version=24` |

The generator checks both `--target-bc-version` and the actual ABC header, size and
Adler-32 checksum. This is one current compiler targeting six bytecode formats, not
six historical SDKs. The current VM provides a same-toolchain oracle, not proof of
compatibility with historical device runtimes. Profiles are `baseline` (`-O0`),
`debug-info` (`-O0 --debug-info`) and `optimized` (`-O2`).

## Test contents

The corpus includes all direct JS/TS source inputs under the selected upstream
`bytecode`, `optimizer` and `type_extractor` suites, plus project fixtures covering
arithmetic, branches/loops, closures, try/catch/finally, MUTF-8, literal arrays,
accessors/inheritance, generators, enums, module exports/imports. Each source is
compiled for all six versions and profiles when its declared compiler mode supports it.
Tags identify `file`, `isa`, `ir` and feature coverage. This is a direct-use compiler
corpus, not an assertion of complete ISA coverage.

The corpus also contains a test262 P0 subset: arkcompiler's own curated CI list
(`arkcompiler/ets_frontend/test262/CI_tests.txt`, 3,968 entries — 3,963 after deduplicating 5 repeats in the list itself) run against
`tc39/test262` pinned at commit `747bed2e8aaafe8fdf2c65e8a10dd7ae64f66c47`
(BSD-3-Clause; the license text ships under `licenses/test262-LICENSE`, and the pin
plus the verified git tree hash live in `upstream.lock.json`). Each test is
preprocessed per upstream INTERPRETING rules: `harness/sta.js` and
`harness/assert.js` are inlined and a single strict-mode pass is produced by
prepending `"use strict";`. Tests needing more than that are counted skip buckets,
never silent drops: `skipped-flags` (`raw`/`noStrict`), `skipped-module`,
`skipped-async`, `skipped-includes` (any `includes:` beyond sta/assert) and
`skipped-missing` (CI-list entries absent at the pinned commit). Compilable tests
are built at `24.0.0.0`/`baseline` only, disassembled, and run once under
`ark_js_vm` with the raw exit/stdout/stderr/timeout recorded
(`runtime.status == "recorded"` — a behavior record, not a test262 conformance
claim). es2abc rejections are expected-behavior signals: `negative.phase: parse`
tests land in `expected-negative-parse` with an `agreement` field comparing the
stderr error type against the declared one; other rejections land in
`es2abc-cant`. Every bucket is counted in `corpus/summary.json`.

Forty taint-analysis probes (`cases/probes/`, from abcd-rs `probes-taint`) and five
`yield*` delegation fixtures (`cases/yield-star/`, from abcd-rs
`decompile-fixtures/yield-star`) are included as project (Apache-2.0) structural
cases — compile + disassemble only — pinned to `24.0.0.0`/`baseline` to keep the
corpus volume flat.

`version_control/` source cases are included with their upstream API-specific target
arguments. This preserves real version gating for API11, API12 beta1, API12 beta3,
API18, API20 and API24 instead of compiling every feature as API24. Run
`python3 scripts/audit_isa.py <isa.yaml> <corpus>` to inspect mnemonic coverage per
ABC version. The report distinguishes source-reachable instructions from deprecated,
runtime-internal and wide encodings that require another producer.

The six `ets_runtime/test/executiontest/js` inputs are included as structural
compiler cases; they call OpenHarmony-only host functions such as `terminate` and
`signal`, so they are not assigned a VM oracle. The runtime regression directory is
runner/configuration data rather than standalone source. Likewise, `testTs` is
mostly expected-text output, not 402 independent TypeScript inputs, and is not
mislabelled as executable corpus. The image also contains 41 directly useful `.pa`
references from runtime-core `checked` and `regression` under
`corpus/pandasm/`.

Upstream expected stdout is used where available. Project fixtures have authored
expected stdout; outputs are never approved merely because Ark produced them.
Sources with intentional unresolved imports or throwing constructors are explicitly
marked structural-only. Their bytecode/disassembly remains ready to consume.
Static Panda assembly, C++ unit-test bodies, unavailable Test262/TS corpora and
unconfigured SDK projects are not packaged as supposedly runnable tests. Every PA
included is an actual disassembly of the corresponding generated dynamic ABC.

## Export fixtures for `cargo test`

```sh
mkdir -p exports
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$PWD/exports:/work" arkcompiler-test export /work/corpus

# Or export a small selection to a new empty directory:
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$PWD/exports:/work" arkcompiler-test export /work/small \
  --version 24.0.0.0 --profile baseline --case local/arithmetic
```

The export contains `index.jsonl`, source files, notices and
`<version>/<case>/<profile>/{input.abc,reference.pa,metadata.json}`. All index file
paths are relative to the export root; historical command lines in metadata are
provenance, not paths that consumers must follow. Exports refuse nonempty targets.

The project-side harness can read one index row at a time:

1. `abcd-file`: decode `row.abc`, check its version and metadata, then encode/decode
   round-trip. Entity offsets/checksums may change; compare semantic fields.
2. `abcd-isa`: decode and re-encode method instructions across all six versions.
   Preserve control-flow targets and operand values; don't compare file offsets.
3. `abcd-ir`: lift/optimize/lower candidate ABC, then use the runtime oracle below
   for rows where `runtime.status == "passed"`.

Normal Rust unit tests can use an exported corpus without Docker. A separate
integration job uses Docker only for reference tools/oracles. This repo does not
modify `abcd-rs` or claim to have added a Cargo test harness there.

## Black-box comparison of project-produced ABC

```sh
# candidate.abc was rewritten by abcd-rs from local/arithmetic, baseline, v24.
docker run --rm --network none -v "$PWD/exports:/work" arkcompiler-test \
  compare /work/candidate.abc --case local/arithmetic --version 24.0.0.0
```

`compare` checks header/version, asks Ark to disassemble the candidate, runs it, and
compares exit code/stdout/stderr/timeout against the stored oracle. Mismatch exits
nonzero. This checks the tested behavior, not full program equivalence. Raw pandasm
is preserved; the image does not delete line tables, remap string IDs or claim that
matching pandasm text is a correct semantic equivalence test.

Other interfaces (JSON output; `list` uses JSONL):

```sh
docker run --rm arkcompiler-test info
docker run --rm arkcompiler-test list --version 12.0.2.0 --profile debug-info
docker run --rm arkcompiler-test verify --execute
docker run --rm -v "$PWD/exports:/work" arkcompiler-test \
  compile /work/new.js /work/new.abc --version 12.0.6.0
docker run --rm -v "$PWD/exports:/work" arkcompiler-test \
  compile-matrix /work/new.js /work/new-matrix
docker run --rm -v "$PWD/exports:/work" arkcompiler-test \
  disassemble /work/new.abc /work/new.pa
docker run --rm -v "$PWD/exports:/work" arkcompiler-test run /work/new.abc --timeout 5
docker run --rm -v "$PWD/exports:/work" arkcompiler-test inspect /work/new.abc
```

`inspect` reports only independently checked header fields; it does not invent
method/string counts or a per-file minimum version that ABC headers do not contain.
`run` reports a JSON result and exits nonzero on VM failure/timeout. Tools may also
be called directly with `docker run --entrypoint es2abc arkcompiler-test --help`.

## CI and reproducibility

**CI is radar-only; build/publish is a human act on dabai.** GitHub Actions never
builds or publishes the image. `.github/workflows/upstream-radar.yml` runs weekly
(Monday 00:00 UTC, plus `workflow_dispatch`) and polls the OpenHarmony manifest
repo (`gitee.com/ark_standalone_build/manifest`) for the newest `OpenHarmony-*`
ref by tip committer date. Drift against `upstream.lock.json`'s `manifest_tag`
produces ONE standing PR per ref (closing it means wont-port) containing the human
checklist below; radar infrastructure failures produce ONE standing issue. The
radar never gates, never builds, never syncs and never auto-merges. The remaining
CI job (`check.yml`) only compiles the Python and runs the header unit tests.

When the radar (or a human) accepts a new manifest ref, the manual flow is:

1. Sync the monorepo on dabai (`repo init -b <ref>` / `repo sync` in the checkout).
2. `python3 ark.py x64.release es2panda ark_disasm ark_js_vm -j8`.
3. `python3 scripts/prepare.py --update-lock`; review and commit the lock changes.
4. `make build && make test`.
5. Merge the PR so main carries the new lock.
6. `make push` to publish the image.
7. Bump the pinned image digest in abcd-rs.

Digest discipline: consumers pin the image by digest
(`ghcr.io/fxti/arkcompiler-test@sha256:...`), never `:latest`. Export once per job,
run Rust fixture tests, then invoke `compare` on rewritten artifacts. Retain failed
candidate ABC, the corresponding manifest row and raw PA as CI artifacts.
`make test` exercises the image offline, with a read-only root filesystem and a
non-root user: all oracles, six-version export, altered-output mismatch, incorrect
version, timeout and corrupted fixture rejection.

`build-info.json` records upstream revisions, selected-source/ISA hashes, binary
hashes and build mode. `index.jsonl` records source/ABC/PA hashes, exact compilation
arguments and tool outputs. Stable source paths are used during generation.
Base/apt repositories may change; this is traceable artifact generation, not a claim
that upstream binaries or arbitrary future Docker builds are byte-reproducible.

## Build and push locally

The image is built and pushed locally; CI is not required. After staging the artifacts
on a machine with the OpenHarmony checkout, run:

```sh
make build
```

Log in to the target registry using its normal Docker credentials. For GHCR:

```sh
echo "$CR_PAT" | docker login ghcr.io -u FXTi --password-stdin
make push
```

`make build` generates and verifies the complete corpus inside Docker. `make push`
pushes the already-built `arkcompiler-test:latest` to the registry as `latest`.
The published image is:

```text
ghcr.io/fxti/arkcompiler-test:latest
```

Make the GHCR package public in GitHub package settings if consumers should pull it
without credentials. The image build and push do not depend on GitHub Actions.

## TODO

- Add source-level cases for remaining reachable ISA gaps: call range/name forms,
  iterator-close paths, computed index/property forms, RegExp literal lowering,
  async iteration and super/private property paths.
- Add static-core/ETS ABC fixtures and their version matrix if static-file support
  is enabled in `abcd-rs`.
- Add deterministic malformed ABC fixtures for truncated sections, bad checksums,
  invalid versions, out-of-range entities, malformed exception tables and invalid
  jump targets. Fuzz corpora remain excluded.
- Add normalized-pandasm comparison and an `ark_asm` producer if an assembler is
  built; current `.pa` files are reference-only.
- Expand runtime oracle expectations for async scheduling and OpenHarmony host APIs.
- Revisit TypeScript expected-text conformance; widen test262 beyond the P0 list
  (per-test `includes:`, `async` and `module` flags, non-strict pass) after defining
  harness composition and volume budgets.

## License

Apache-2.0 for this repository. Upstream source headers are retained verbatim.
Upstream and third-party notices accompany the image and exported corpus under
`licenses/`; upstream compilation outputs retain their source provenance.
