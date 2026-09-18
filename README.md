# json-bench

Benchmarks for JSON libraries across Zig, C, C++, and Rust, on real-world data.

Each implementation is compared per task, and each chart only includes the
implementations that support that task. Typed tasks compare the Zig libraries;
DOM tasks also bring in native C, C++, and Rust parsers.

## Implementations

| Name | Language | Project | Tasks |
| --- | --- | --- | --- |
| `jsonz` | Zig | [KercyDing/jsonz](https://github.com/KercyDing/jsonz) | all |
| `std.json` | Zig | Zig standard library | all |
| `serde` | Zig | [OrlovEvgeny/serde.zig](https://github.com/OrlovEvgeny/serde.zig) | encode, decode |
| `yyjson` | C | [ibireme/yyjson](https://github.com/ibireme/yyjson) | load, transform, get |
| `simdjson` | C++ | [simdjson/simdjson](https://github.com/simdjson/simdjson) | load, get |
| `glaze` | C++ | [stephenberry/glaze](https://github.com/stephenberry/glaze) | load, transform, get |
| `serpent` | C++ | [nebkat/cpp-serpent](https://github.com/nebkat/cpp-serpent) | all |
| `sonic-rs` | Rust | [bytedance/sonic-rs](https://github.com/bytedance/sonic-rs) | load, transform, get |

`serde.zig` has no DOM, so it is left out of the DOM tasks instead of being
compared on an API it does not have. `simdjson` is read-only, so it has no
`transform`. `serpent` names a type's fields by reflection, so it runs the
typed tasks against the same schemas the Zig libraries use (`cpp/serpent_types.hpp`
mirrors `zig/shared.zig` field for field); it needs GCC 16, see below.

## Tasks

| Task | Token | What it measures |
| --- | --- | --- |
| Encode known data | `known-encode` | Serialize a typed Zig value. |
| Decode known data | `known-decode` | Parse into a typed Zig value. |
| Load arbitrary data | `arbitrary-decode` | Parse into a DOM. |
| Transform data | `transform` | Parse into a DOM and serialize it back. |
| Get element | `get` | Read one nested element through the library's own access API. |

The first two need a schema, so they only run for the Zig libraries and
`serpent`.

## Methodology

- **One-shot owning parse.** Each timed iteration creates the storage that owns
  the parsed result. Reading the input and freeing the result are excluded.
- **Input ownership is not normalized.** `yyjson_read` lets the DOM's strings
  point into the caller's input, so it never copies the input; `jsonz` copies it
  (in-place escape decoding needs a mutable, padded copy), `simdjson` builds a
  fresh `padded_string`, and `simd-json` clones the buffer. The timed region
  includes whatever the implementation itself does, copy included — this is a
  real design difference, not a shared cost.
- **`get` reads from whatever the library holds.** Every implementation parses
  the document once before the clock starts and the timed region is the lookup
  alone. `serpent` has no DOM, so what it holds is a `json::structural_index` —
  where every value is, recorded in that same one pass.
- **`get` amortizes the clock.** A single access is only tens of nanoseconds, so
  every sample resolves the element 1024 times between the two clock reads. The
  clock overhead is then under 0.1% of the reported `ns/op`. The other tasks are
  millisecond-scale and time one operation per sample.
- **Processes and medians.** Each implementation runs in its own process,
  `--runs` times, with the order rotated between runs and a warmup before each
  thread count; results are medians across processes.

## Quick start

`mise` pins the Zig version; CMake fetches simdjson, Glaze and serpent, and
Cargo fetches the Rust crates, so the first run needs network access.

```sh
python3 bench.py
```

serpent's reflected path needs GCC 16 with `-std=c++26 -freflection`, so the
whole C/C++ build is configured with that compiler and every implementation is
compared under one. `bench.py` passes `gcc-16`/`g++-16`; `CC` and `CXX`
override them. serpent is also a private repository fetched over SSH — build
from a local checkout instead with

```sh
cmake --preset release -DFETCHCONTENT_SOURCE_DIR_SERPENT=/path/to/cpp-serpent
```

`python3 bench.py --without-serpent` leaves serpent out, and with it the GCC 16
requirement.

Nothing in the timing harness compares outputs, so serpent's benchmark carries
its own check — that every typed value, tree and resolved element comes back as
the corpus says it is, against literals read out with an unrelated parser:

```sh
./build/serpent_bench --verify
```

`bench.py` builds every language, runs each implementation, aggregates the runs
by median, writes `results/json/{summary.csv,summary.md,index.html}`, and opens
the report.

## Options

| Option | Description |
| --- | --- |
| `--runs N` | Independent process runs per implementation (default: 3). |
| `--parallel [THREADS]` | Also measure 1, 2, 4, ... processes at once, up to `THREADS`, and chart the scaling. |
| `--no-build` | Regenerate the reports from `results/json/measurements.json`. |
| `--without-serpent` | Leave serpent out, and with it the GCC 16 requirement. |

## Layout

| Path | Contents |
| --- | --- |
| `bench.py` | Build, run, and report driver. |
| `CMakePresets.json` | The `release` preset (`Unix Makefiles`, `Release`, `build/`) shared by `bench.py` and editors; keeps cached generator/build type from drifting. |
| `zig/` | jsonz, std.json and serde.zig adapters, sharing the `bench.zig` harness. |
| `c/` | yyjson benchmark. |
| `cpp/` | simdjson, Glaze and serpent benchmarks, sharing `cpp/bench.hpp`. |
| `rust/` | sonic-rs benchmark. |
| `data/json/` | Corpus. |
| `results/` | Generated reports (gitignored). |

## Get targets

The `get` task reads one nested element per dataset with each library's own
access API: `ptrGet` (RFC 6901) for jsonz, `at_pointer` for simdjson, `yyjson_ptr_get` for yyjson, and native
object/array accessors for the rest — for serpent that is `indexed_reader::operator[]` over a `json::structural_index`.

| Dataset | Pointer |
| --- | --- |
| `canada.json` | `/features/0/geometry/coordinates/0/0` |
| `citm_catalog.json` | `/areaNames/205705993` |
| `fgo.json` | `/mstSvt/0/relateQuestIds/0` |
| `github_events.json` | `/0/actor/login` |
| `gsoc-2018.json` | `/0/name` |
| `lottie.json` | `/assets/0/layers/0/nm` |
| `otfcc.json` | `/head/version` |
| `poet.json` | `/0/name` |
| `twitter.json` | `/statuses/0/user/id` |
| `twitterescaped.json` | `/statuses/0/user/id` |
