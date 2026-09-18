"""Run json-bench and open the interactive result pages.

Five user tasks are measured, each run by the JSON implementations that
support it:

* Encode known data      (known-encode)     Zig and serpent
* Decode known data      (known-decode)     Zig and serpent
* Load arbitrary data    (arbitrary-decode)
* Transform data         (transform)
* Get element            (get)

The Zig programs already perform warmup and repeat each fixture according to
its size and print one task-tagged metric line per dataset. This driver runs
every implementation in separate processes, keeps the raw text for
auditability, aggregates process runs by median, and renders one web page
(``results/json/index.html``) with Highcharts column charts — for each task two
side-by-side charts: throughput in GB/s and rate in ops/s. With
``--parallel`` it also starts 1, 2, 4, ... processes at once and adds line
charts per task: aggregate throughput and speedup against the thread count.
Then it opens the generated pages in your browser.
No Python chart library is needed; the page loads Highcharts from a CDN (first
open requires network).

Typical use::

    uv run bench.py               # run all, write html + csv + md, open page
    uv run bench.py --no-build    # regenerate reports from existing results

Raw per-process output and ``measurements.json`` are kept under the result
format directory; the HTML, CSV, and Markdown reports are derived from them.
"""

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import webbrowser
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO, TypedDict, cast

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "results"


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def machine_threads() -> int:
    """Threads this process may run on, honouring the affinity mask."""
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            return max(1, len(affinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def thread_counts(limit: int) -> tuple[int, ...]:
    """The measured thread counts: 1, 2, 4, ... up to and including ``limit``."""
    counts = []
    count = 1
    while count < limit:
        counts.append(count)
        count *= 2
    counts.append(limit)
    return tuple(counts)


def run_concurrently(
    *,
    label: str,
    command: Sequence[str],
    threads: int,
    raw_dir: Path,
    stem: str,
) -> list[str]:
    """Start ``threads`` copies of ``command`` at once; return one output per
    process, each also written to ``raw_dir`` like a single run.

    Output goes to files rather than pipes so that a talkative process cannot
    block on a full pipe while another one is still being drained.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    processes: list[subprocess.Popen[bytes]] = []
    handles: list[TextIO] = []
    paths: list[Path] = []
    try:
        for index in range(1, threads + 1):
            path = raw_dir / f"{stem}-p{index:02d}.txt"
            handle = path.open("w", encoding="utf-8")
            handles.append(handle)
            paths.append(path)
            processes.append(
                subprocess.Popen(
                    list(command),
                    cwd=ROOT,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            )
    finally:
        for handle in handles:
            handle.close()

    codes = [process.wait() for process in processes]
    outputs = [path.read_text(encoding="utf-8") for path in paths]
    for index, (code, output) in enumerate(zip(codes, outputs), start=1):
        if code == 0:
            continue
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(
            f"[{label}] process {index}/{threads} failed with exit code {code}:\n{tail}"
        )
    return outputs


def run_warmup(command: Sequence[str]) -> None:
    """Run one implementation once to populate build/runtime caches."""
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        output = completed.stdout.decode(errors="replace")
        raise RuntimeError(
            f"warmup failed with exit code {completed.returncode}:\n{output}"
        )


ALL_DATASETS = (
    "small.json",
    "canada.json",
    "citm_catalog.json",
    "fgo.json",
    "github_events.json",
    "gsoc-2018.json",
    "lottie.json",
    "otfcc.json",
    "poet.json",
    "twitter.json",
    "twitterescaped.json",
)
KNOWN_DATASETS = frozenset(
    {"small.json", "canada.json", "github_events.json", "poet.json", "twitter.json", "twitterescaped.json"}
)
ARBITRARY_DATASETS = frozenset(ALL_DATASETS) - {"small.json"}
FORMAT = "json"
FORMAT_LABEL = "JSON"
IMPLEMENTATIONS = ("jsonz", "std.json", "serde", "yyjson", "simdjson", "glaze", "serpent", "sonic-rs")
TASKS = (
    ("Encode known data", "known-encode"),
    ("Decode known data", "known-decode"),
    ("Load arbitrary data", "arbitrary-decode"),
    ("Transform data", "transform"),
    ("Get element", "get"),
)
TASK_LABELS = {token: label for label, token in TASKS}
# `get` reads a single element, so byte throughput over the input is meaningless.
NO_THROUGHPUT_TASKS = frozenset({TASK_LABELS["get"]})
ALL_TOKENS = tuple(token for _, token in TASKS)
JSON_TOKENS = {
    "serde": ("known-encode", "known-decode"),
    "jsonz": ALL_TOKENS,
    "std.json": ALL_TOKENS,
    "yyjson": ("arbitrary-decode", "transform", "get"),
    "simdjson": ("arbitrary-decode", "get"),
    "glaze": ("arbitrary-decode", "transform", "get"),
    "serpent": ALL_TOKENS,
    "sonic-rs": ("arbitrary-decode", "transform", "get"),
}
RUN_COMMANDS = {
    "serde": ["zig-out/bin/serde_bench"],
    "jsonz": ["zig-out/bin/jsonz_bench"],
    "std.json": ["zig-out/bin/std_bench"],
    "yyjson": ["./build/yyjson_bench"],
    "simdjson": ["./build/simdjson_bench"],
    "glaze": ["./build/glaze_bench"],
    "serpent": ["./build/serpent_bench"],
    "sonic-rs": ["./target/release/sonic"],
}
# serpent names a type's fields by reflection, which today means GCC 16 with -std=c++26
# -freflection. The whole C/C++ build uses that compiler so every implementation is compared
# under one; CC and CXX override it, and --without-serpent drops the requirement entirely.
DEFAULT_C_COMPILER = "gcc-16"
DEFAULT_CXX_COMPILER = "g++-16"


def implementation_directory(implementation: str) -> str:
    return implementation


def supported_tokens(implementation: str) -> tuple[str, ...]:
    """Metric tokens that an implementation prints."""
    return JSON_TOKENS[implementation]


def token_datasets(token: str) -> frozenset[str]:
    """Datasets where a token produces a metric.

    Known-schema tasks cover their applicable fixtures; the remaining tasks
    cover every dataset except the typed-only tiny fixture.
    """
    if token in ("known-encode", "known-decode"):
        return KNOWN_DATASETS
    return ARBITRARY_DATASETS


DATASET_RE = re.compile(
    r"^\s*(?P<dataset>\S+)\s+\((?P<input_bytes>\d+) bytes,\s+(?P<repeats>\d+) repeats\)\s*$"
)
METRIC_RE = re.compile(
    r"^\s*(?P<label>[^:]+):\s+"
    r"(?P<milliseconds>[0-9]+(?:\.[0-9]+)?)\s+ms/op,\s+"
    r"(?P<reported_mib>[0-9]+(?:\.[0-9]+)?)\s+MiB/s"
    r"(?:\s+\((?P<output_bytes>\d+) bytes\))?\s*$"
)


@dataclass(frozen=True)
class Measurement:
    """One metric line from one benchmark process."""

    format: str
    implementation: str
    dataset: str
    task: str
    input_bytes: int
    output_bytes: int | None
    milliseconds: float
    reported_mib_s: float
    run: int
    threads: int = 1

    @property
    def measured_bytes(self) -> int:
        if self.output_bytes is not None:
            return self.output_bytes
        return self.input_bytes


class SummaryRow(TypedDict):
    format: str
    implementation: str
    dataset: str
    task: str
    input_bytes: int
    output_bytes: int | None
    measured_bytes: int
    runs: int
    threads: int
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    throughput_mib_s: float
    throughput_gb_s: float
    latency_ns: float
    ops_per_s: float
    speedup: float


def parse_label(label: str) -> tuple[str, str]:
    """Split a metric label into (implementation, token).

    Labels look like "serde known-encode" or "std.json transform"; anything
    without a known prefix is treated as serde and the trailing word is the
    token candidate.
    """
    for implementation in IMPLEMENTATIONS:
        prefix = implementation + " "
        if label.startswith(prefix):
            return implementation, label[len(prefix) :].strip()
    return "serde", label.strip()


def parse_output(output: str, run: int) -> list[Measurement]:
    """Parse benchmark output without depending on stdout/stderr ordering."""

    current_dataset: str | None = None
    current_input_bytes: int | None = None
    measurements: list[Measurement] = []

    for line in output.splitlines():
        dataset_match = DATASET_RE.match(line)
        if dataset_match:
            current_dataset = dataset_match.group("dataset")
            current_input_bytes = int(dataset_match.group("input_bytes"))
            continue

        metric_match = METRIC_RE.match(line)
        if not metric_match:
            continue
        if current_dataset is None or current_input_bytes is None:
            raise RuntimeError(f"found metric before a dataset header: {line!r}")

        label = metric_match.group("label").strip().lower()
        implementation, token = parse_label(label)
        task = TASK_LABELS.get(token)
        if task is None:
            continue
        output_bytes = metric_match.group("output_bytes")
        measurements.append(
            Measurement(
                format=FORMAT,
                implementation=implementation,
                dataset=current_dataset,
                task=task,
                input_bytes=current_input_bytes,
                output_bytes=int(output_bytes) if output_bytes is not None else None,
                milliseconds=float(metric_match.group("milliseconds")),
                reported_mib_s=float(metric_match.group("reported_mib")),
                run=run,
            )
        )

    return measurements


def build_all(zig: str, optimize: str, with_serpent: bool) -> None:
    """Build every language once, before any measurement."""
    run_warmup([zig, "build", f"-Doptimize={optimize}", "-Dcpu=native"])
    # CMakePresets.json pins generator, build type, and binary dir so an editor
    # session and this script always share one configuration.
    configure = [
        "cmake",
        "--preset",
        "release",
        f"-DCMAKE_C_COMPILER={os.environ.get('CC', DEFAULT_C_COMPILER)}",
        f"-DCMAKE_CXX_COMPILER={os.environ.get('CXX', DEFAULT_CXX_COMPILER)}",
        f"-DJSON_BENCH_SERPENT={'ON' if with_serpent else 'OFF'}",
    ]
    run_warmup(configure)
    run_warmup(["cmake", "--build", "--preset", "release"])
    run_warmup(["cargo", "build", "--release", "--bins"])


def expected_measurements(implementation: str) -> set[tuple[str, str]]:
    return {
        (dataset, TASK_LABELS[token])
        for token in supported_tokens(implementation)
        for dataset in token_datasets(token)
    }


def validate_measurements(
    measurements: Sequence[Measurement],
    format_name: str,
    implementation: str,
) -> None:
    actual = [(item.dataset, item.task) for item in measurements]
    if len(actual) != len(set(actual)):
        raise RuntimeError(f"{format_name}/{implementation}: duplicate metric lines in benchmark output")

    expected = expected_measurements(implementation)
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(f"{dataset}/{task}" for dataset, task in missing))
        if unexpected:
            details.append("unexpected " + ", ".join(f"{dataset}/{task}" for dataset, task in unexpected))
        raise RuntimeError(f"{format_name}/{implementation}: " + "; ".join(details))


def run_benchmarks(
    runs: int,
    parallel_limit: int | None,
    zig: str,
    optimize: str,
    output_dir: Path,
    with_serpent: bool = True,
) -> tuple[list[Measurement], dict[str, list[str]]]:
    """Run every (format, implementation) once per thread count.

    Without ``parallel_limit`` that is a single process per run, as before. With
    it, each run starts ``threads`` processes at once for every count in
    ``thread_counts(parallel_limit)``; the outputs of one run are aggregated into
    one sample per thread count. Implementations are warmed up before each thread
    count and rotated between runs to reduce order-dependent system noise.
    """
    counts = (1,) if parallel_limit is None else thread_counts(parallel_limit)
    all_measurements: list[Measurement] = []
    commands = {implementation: list(RUN_COMMANDS[implementation]) for implementation in IMPLEMENTATIONS}

    build_all(zig, optimize, with_serpent)

    for threads in counts:
        print(f"=== warmup at {threads} thread(s) ===", flush=True)
        for implementation in IMPLEMENTATIONS:
            run_warmup(commands[implementation])

        for run in range(1, runs + 1):
            # Rotate the order each round so no implementation is always first
            # or last. Keep concurrent processes within one implementation.
            offset = (run - 1) % len(IMPLEMENTATIONS)
            order = IMPLEMENTATIONS[offset:] + IMPLEMENTATIONS[:offset]
            for implementation in order:
                raw_dir = output_dir / FORMAT / implementation_directory(implementation)
                command = commands[implementation]
                label = f"{FORMAT}/{implementation}"
                stem = "tasks" if counts == (1,) else f"tasks-t{threads:02d}"
                print(
                    f"[{label}] run {run}/{runs} at {threads} thread(s): {' '.join(command)}",
                    flush=True,
                )
                outputs = run_concurrently(
                    label=f"{label} t{threads}",
                    command=command,
                    threads=threads,
                    raw_dir=raw_dir,
                    stem=f"{stem}-{run:02d}",
                )
                parsed_count = 0
                for output in outputs:
                    parsed = parse_output(output, run)
                    validate_measurements(parsed, FORMAT, implementation)
                    all_measurements.extend(replace(item, threads=threads) for item in parsed)
                    parsed_count += len(parsed)
                print(
                    f"  parsed {parsed_count} measurements at {threads} thread(s)",
                    flush=True,
                )
    return all_measurements, commands


def aggregate(measurements: Iterable[Measurement]) -> list[SummaryRow]:
    """One row per (format, implementation, dataset, task, threads).

    A run at ``threads`` threads starts that many processes at once, so a run's
    aggregate rate is the sum of its processes' rates and its latency is the
    median per-op time; a row is the median of those aggregates across runs.
    """
    groups: dict[tuple[str, str, str, str, int], dict[int, list[Measurement]]] = {}
    for measurement in measurements:
        key = (
            measurement.format,
            measurement.implementation,
            measurement.dataset,
            measurement.task,
            measurement.threads,
        )
        groups.setdefault(key, {}).setdefault(measurement.run, []).append(measurement)

    rows: list[SummaryRow] = []
    rates: dict[tuple[str, str, str, str, int], float] = {}
    for key in sorted(groups):
        format_name, implementation, dataset, task, threads = key
        runs = groups[key]
        measured_bytes = {
            sample.measured_bytes for samples in runs.values() for sample in samples
        }
        if len(measured_bytes) != 1:
            raise RuntimeError(f"{format_name}/{task}/{dataset}: payload size changed between runs")
        measured = measured_bytes.pop()

        batch_rates: list[float] = []
        batch_ops: list[float] = []
        batch_ms: list[float] = []
        for samples in runs.values():
            batch_rates.append(
                sum(sample.measured_bytes / (max(sample.milliseconds, 1e-9) / 1000.0) for sample in samples)
            )
            batch_ops.append(sum(1000.0 / max(sample.milliseconds, 1e-9) for sample in samples))
            batch_ms.append(statistics.median(sample.milliseconds for sample in samples))

        durations = [sample.milliseconds for samples in runs.values() for sample in samples]
        throughput_mib_s = statistics.median(batch_rates) / (1024.0 * 1024.0)
        rates[key] = throughput_mib_s
        rows.append(
            {
                "format": format_name,
                "implementation": implementation,
                "dataset": dataset,
                "task": task,
                "threads": threads,
                "input_bytes": next(iter(runs.values()))[0].input_bytes,
                "output_bytes": next(iter(runs.values()))[0].output_bytes,
                "measured_bytes": measured,
                "runs": len(runs),
                "median_ms": statistics.median(batch_ms),
                "min_ms": min(durations),
                "max_ms": max(durations),
                "stdev_ms": statistics.stdev(durations) if len(durations) > 1 else 0.0,
                "throughput_mib_s": throughput_mib_s,
                "throughput_gb_s": throughput_mib_s * (1024.0 * 1024.0) / 1_000_000_000.0,
                "latency_ns": statistics.median(batch_ms) * 1_000_000.0,
                "ops_per_s": statistics.median(batch_ops),
                "speedup": 0.0,
            }
        )

    for row in rows:
        baseline = rates.get(
            (
                str(row["format"]),
                str(row["implementation"]),
                str(row["dataset"]),
                str(row["task"]),
                1,
            )
        )
        row["speedup"] = float(row["throughput_mib_s"]) / baseline if baseline else 0.0
    return rows


def write_csv(path: Path, rows: Sequence[SummaryRow]) -> None:
    fields = [
        "format",
        "implementation",
        "dataset",
        "task",
        "threads",
        "input_bytes",
        "output_bytes",
        "measured_bytes",
        "runs",
        "median_ms",
        "min_ms",
        "max_ms",
        "stdev_ms",
        "throughput_mib_s",
        "throughput_gb_s",
        "latency_ns",
        "ops_per_s",
        "speedup",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: Sequence[SummaryRow], runs: int) -> None:
    by_key = {
        (str(row["implementation"]), str(row["dataset"]), str(row["task"])): row
        for row in rows
        if int(row.get("threads", 1)) == 1
    }

    lines = [
        "# json-bench results",
        "",
        (
            f"Median of {runs} run(s). Throughput is the median of the per-run aggregate "
            "rates over the measured payload (encoded output bytes when reported, "
            "otherwise input bytes); latency is the median `ns/op`."
        ),
        "",
    ]
    for task, _ in TASKS:
        implementations = [
            implementation
            for implementation in IMPLEMENTATIONS
            if any((implementation, dataset, task) in by_key for dataset in ALL_DATASETS)
        ]
        if not implementations:
            continue
        task_datasets = [
            dataset
            for dataset in ALL_DATASETS
            if any((implementation, dataset, task) in by_key for implementation in implementations)
        ]
        show_throughput = task not in NO_THROUGHPUT_TASKS
        headers = []
        for implementation in implementations:
            if show_throughput:
                headers.append(f"{implementation} GB/s")
            headers += [f"{implementation} ns/op", f"{implementation} ops/s"]
        lines.extend(
            [
                f"## {FORMAT_LABEL} · {task}",
                "",
                "| Dataset | " + " | ".join(headers) + " |",
                "| --- | " + " | ".join("---:" for _ in headers) + " |",
            ]
        )
        for dataset in task_datasets:
            values: list[str] = []
            for implementation in implementations:
                row = by_key.get((implementation, dataset, task))
                if row is None:
                    values += ["-"] * (3 if show_throughput else 2)
                    continue
                if show_throughput:
                    values.append(f"{float(row['throughput_gb_s']):.3f}")
                values += [
                    f"{float(row['latency_ns']):.2f}",
                    f"{float(row['ops_per_s']):,.0f}",
                ]
            lines.append(f"| {dataset.removesuffix('.json')} | " + " | ".join(values) + " |")
        lines.append("")

    lines.extend(scaling_markdown(rows))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scaling_markdown(rows: Sequence[SummaryRow]) -> list[str]:
    """One table per task: aggregate rate and speedup per thread count."""
    parallel = [row for row in rows if int(row.get("threads", 1)) > 1]
    if not parallel:
        return []
    totals = thread_totals(rows)
    counts = sorted({threads for (_, _, threads) in totals})
    baselines = {
        (task, implementation): total
        for (task, implementation, threads), total in totals.items()
        if threads == 1
    }

    lines = [
        "## Parallel scaling",
        "",
        (
            "Aggregate rate per thread count, summed over each task's datasets, with "
            "the speedup relative to one thread in parentheses. Element reads are "
            "summed in operations per second instead of bytes per second."
        ),
        "",
    ]
    for task, _ in TASKS:
        implementations = [
            implementation
            for implementation in IMPLEMENTATIONS
            if (task, implementation, 1) in totals
        ]
        if not implementations:
            continue
        unit = "ops/s" if task in NO_THROUGHPUT_TASKS else "GB/s"
        lines.extend(
            [
                f"### {FORMAT_LABEL} · {task} ({unit})",
                "",
                "| Implementation | " + " | ".join(f"{count} threads" for count in counts) + " |",
                "| --- | " + " | ".join("---:" for _ in counts) + " |",
            ]
        )
        for implementation in implementations:
            cells: list[str] = []
            baseline = baselines[(task, implementation)]
            for count in counts:
                total = totals.get((task, implementation, count), 0.0)
                rate = f"{total:,.0f}" if task in NO_THROUGHPUT_TASKS else f"{total:.2f}"
                cells.append(f"{rate} ({total / baseline:.2f}×)")
            lines.append(f"| {implementation} | " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def thread_totals(rows: Sequence[SummaryRow]) -> dict[tuple[str, str, int], float]:
    """Aggregate rate summed over datasets, per (task, implementation, threads).

    Tasks that move no payload (`NO_THROUGHPUT_TASKS`) are summed in operations
    per second instead of bytes per second.
    """
    totals: dict[tuple[str, str, int], float] = {}
    for row in rows:
        key = (str(row["task"]), str(row["implementation"]), int(row.get("threads", 1)))
        scale = float(row["ops_per_s"]) if str(row["task"]) in NO_THROUGHPUT_TASKS else float(row["throughput_gb_s"])
        totals[key] = totals.get(key, 0.0) + scale
    return totals


def load_summary(path: Path) -> tuple[list[SummaryRow], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("summary")
    if not isinstance(rows, list):
        raise TypeError(f"{path} does not contain a summary array")
    runs = int(payload.get("metadata", {}).get("runs", 1))
    for row in rows:
        # Older summaries stored microseconds.
        if "latency_ns" not in row:
            row["latency_ns"] = float(row.get("latency_us", 0.0)) * 1000.0
    return cast(list[SummaryRow], rows), runs


HIGHCHARTS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highcharts/8.2.0/"


def write_html_page(path: Path, rows: Sequence[SummaryRow], runs: int) -> None:
    """Write an interactive Highcharts page (library from CDN, like yyjson).

    Layout: for each format, user task and metric (GB/s and ops/s) one column
    chart, with the participating implementations as separate series. When the
    run measured several thread counts, each format also gets one line chart per
    task: aggregate throughput against the thread count, and the speedup it
    bought. No Python chart library.
    """
    single = [row for row in rows if int(row.get("threads", 1)) == 1]
    totals = thread_totals(rows)
    by_key = {
        (str(row["implementation"]), str(row["dataset"]), str(row["task"])): row for row in single
    }
    chart_counter = 0
    # metric key -> (axis title, decimals for tooltips)
    CHART_METRICS = (("gb", "GB/s", 3), ("ops", "ops/s", 0))
    # `get` moves no payload, so it charts the access latency instead.
    LATENCY_CHART_METRICS = (("ns", "ns/op", 2), ("ops", "ops/s", 0))

    def chart_html(
        categories: list[str],
        series: list[dict[str, object]],
        height: int,
        filename: str,
        y_title: str,
        point_format: str,
        *,
        kind: str = "column",
        x_title: str | None = None,
    ) -> str:
        nonlocal chart_counter
        chart_counter += 1
        container_id = f"chart-{chart_counter}"
        x_axis: dict[str, object] = {"categories": categories}
        if x_title is None:
            x_axis["labels"] = {"rotation": -35, "style": {"fontSize": "11px"}}
        else:
            x_axis["title"] = {"text": x_title}
            x_axis["labels"] = {"style": {"fontSize": "11px"}}
        options = {
            "chart": {
                "type": kind,
                "height": height,
                "backgroundColor": "transparent",
                "animation": True,
            },
            "title": {"text": None},
            "credits": {"enabled": False},
            "exporting": {"filename": filename},
            "xAxis": x_axis,
            "yAxis": {"title": {"text": y_title}, "min": 0},
            "tooltip": {"shared": True, "pointFormat": point_format},
            "legend": {"layout": "horizontal", "align": "center", "verticalAlign": "top"},
            "plotOptions": {
                "column": {"borderRadius": 3, "pointPadding": 0.06, "groupPadding": 0.2},
                "line": {"marker": {"enabled": True, "radius": 3}, "lineWidth": 2},
            },
            "series": series,
        }
        return (
            f'<div id="{container_id}" class="hc-container"></div>\n'
            f"<script>Highcharts.chart({json.dumps(container_id)}, {json.dumps(options)});</script>\n"
        )

    def series_points(
        implementation: str,
        task: str,
        datasets: list[str],
        metric: str,
    ) -> list[float | None]:
        def value_for(dataset: str) -> float | None:
            row = by_key.get((implementation, dataset, task))
            if row is None:
                return None
            if metric == "gb":
                return float(row["throughput_gb_s"])
            if metric == "ns":
                return float(row["latency_ns"])
            if metric == "ops":
                return float(row["ops_per_s"])
            raise ValueError(f"unknown metric {metric!r}")

        return [value_for(dataset) for dataset in datasets]

    def collect_series(
        series_specs: list[tuple[str, str, list[str]]],
        metric: str,
    ) -> list[dict[str, object]]:
        return [
            {"name": implementation, "data": series_points(implementation, task, datasets, metric)}
            for implementation, task, datasets in series_specs
        ]

    def card(heading: str, plot: str, description: str = "") -> str:
        detail = "" if not description else f'<p class="chart-note">{description}</p>\n'
        return "<section class=\"plot\">\n" f"<h2>{heading}</h2>\n{detail}{plot}\n" "</section>"

    def scaling_sections(label: str) -> list[str]:
        counts = sorted({threads for (_, _, threads) in totals})
        if len(counts) < 2:
            return []
        result = [
            (
                '<header class="section-header">\n'
                f"<h2>{label} \u00b7 parallel scaling</h2>\n"
                "<p>Aggregate throughput summed over each task's datasets, measured with "
                "1, 2, 4, ... threads in separate processes.</p>\n"
                "</header>"
            )
        ]
        categories = [str(count) for count in counts]
        for task, token in TASKS:
            implementations = [
                implementation
                for implementation in IMPLEMENTATIONS
                if (task, implementation, 1) in totals
            ]
            if not implementations:
                continue
            throughput: list[dict[str, object]] = []
            speedup: list[dict[str, object]] = []
            for implementation in implementations:
                baseline = totals[(task, implementation, 1)]
                rates = [totals.get((task, implementation, count), 0.0) for count in counts]
                throughput.append({"name": implementation, "data": rates})
                speedup.append(
                    {
                        "name": implementation,
                        "data": [rate / baseline for rate in rates],
                    }
                )
            speedup.append(
                {
                    "name": "ideal",
                    "data": list(counts),
                    "dashStyle": "ShortDash",
                    "color": "#a0aec0",
                    "marker": {"enabled": False},
                    "enableMouseTracking": False,
                    "showInLegend": False,
                }
            )
            unit = "ops/s" if task in NO_THROUGHPUT_TASKS else "GB/s"
            point_format = (
                "{series.name}: <b>{point.y:,.0f} ops/s</b><br/>"
                if task in NO_THROUGHPUT_TASKS
                else "{series.name}: <b>{point.y:.3f} GB/s</b><br/>"
            )
            pair = [
                card(
                    f"{label} \u00b7 {task} \u00b7 {unit} by thread count",
                    chart_html(
                        categories,
                        throughput,
                        380,
                        f"{FORMAT}-{token}-scaling",
                        unit,
                        point_format,
                        kind="line",
                        x_title="threads",
                    ),
                ),
                card(
                    f"{label} \u00b7 {task} \u00b7 speedup",
                    chart_html(
                        categories,
                        speedup,
                        380,
                        f"{FORMAT}-{token}-speedup",
                        "speedup \u00d7",
                        "{series.name}: <b>{point.y:.2f}\u00d7</b><br/>",
                        kind="line",
                        x_title="threads",
                    ),
                ),
            ]
            result.append("<div class=\"plot-row\">\n" + "\n".join(pair) + "\n</div>")
        return result

    sections: list[str] = []
    label = FORMAT_LABEL
    implementations = IMPLEMENTATIONS
    for task, token in TASKS:
        group = [
            dataset
            for dataset in ALL_DATASETS
            if any(
                (implementation, dataset, task) in by_key
                for implementation in implementations
            )
        ]
        if not group:
            continue
        implementations_present = [
            implementation
            for implementation in implementations
            if any((implementation, dataset, task) in by_key for dataset in group)
        ]
        if not implementations_present:
            continue
        metrics = LATENCY_CHART_METRICS if task in NO_THROUGHPUT_TASKS else CHART_METRICS
        if task == TASKS[-1][0]:
            # The final task keeps only ops/s; its ns/op chart was redundant.
            metrics = tuple(metric for metric in metrics if metric[0] != "ns")
        pair: list[str] = []
        for metric, unit, decimals in metrics:
            series = collect_series(
                [(implementation, task, group) for implementation in implementations_present],
                metric,
            )
            pair.append(
                card(
                    f"{label} \u00b7 {task} \u00b7 {unit}",
                    chart_html(
                        [dataset.removesuffix(".json") for dataset in group],
                        series,
                        430,
                        f"{FORMAT}-{token}-{metric}",
                        unit,
                        f"{{series.name}}: <b>{{point.y:.{decimals}f}} {unit}</b><br/>",
                    ),
                )
            )
        row_class = "plot-row single" if len(pair) == 1 else "plot-row"
        sections.append(f'<div class="{row_class}">\n' + "\n".join(pair) + "\n</div>")

    sections.extend(scaling_sections(label))

    if not sections:
        raise RuntimeError("no measurements to chart")

    body = "\n".join(sections)
    note = (
        f"Median of {runs} process run(s) · "
        "timed path excludes file loading and cleanup."
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>json-bench</title>
<script src="{HIGHCHARTS_CDN}highcharts.js"></script>
<script src="{HIGHCHARTS_CDN}modules/exporting.js"></script>
<script src="{HIGHCHARTS_CDN}modules/offline-exporting.js"></script>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px 32px 48px; background: #f3f6f9; color: #1f2733; }}
  header {{ max-width: 1000px; margin: 0 auto 22px; text-align: center; }}
  header h1 {{ margin: 0 0 6px; font-size: 24px; color: #141a23; }}
  header p  {{ margin: 0; color: #4a5568; font-size: 14px; }}
  .section-header {{ max-width: 1000px; margin: 30px auto 18px; text-align: center; }}
  .section-header h2 {{ font-size: 20px; margin: 0 0 6px; color: #141a23; }}
  .section-header p {{ margin: 0; color: #4a5568; font-size: 14px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; color: #141a23; }}
  .chart-note {{ margin: -4px 0 12px; color: #4a5568; font-size: 14px; }}
  section.plot {{ max-width: 1000px; margin: 0 auto 28px; background: #ffffff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(16, 24, 40, 0.08); }}
  .plot-row {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px; max-width: 1320px; margin: 0 auto 28px; align-items: start; }}
  .plot-row section.plot {{ max-width: none; margin: 0; }}
  @media (min-width: 901px) {{ .plot-row.single section.plot {{ grid-column: 2; }} }}
  @media (max-width: 900px) {{ .plot-row {{ grid-template-columns: minmax(0, 1fr); }} }}
  .hc-container {{ width: 100%; }}
  footer {{ max-width: 1000px; margin: 4px auto 0; color: #718096; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<header>
  <h1>json-bench</h1>
  <p>{note}</p>
</header>
{body}
<footer>Generated by bench.py; raw logs and measurements.json live alongside this page.</footer>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--runs",
        type=positive_int,
        default=3,
        help="independent process runs per thread count (default: 3)",
    )
    result.add_argument(
        "--parallel",
        nargs="?",
        const=0,
        default=None,
        type=positive_int,
        metavar="THREADS",
        help=(
            "also measure with 1, 2, 4, ... threads up to THREADS "
            "(default when the value is omitted: every machine thread)"
        ),
    )
    result.add_argument(
        "--no-build",
        action="store_true",
        help="regenerate HTML, CSV, and Markdown from existing measurements",
    )
    result.add_argument(
        "--without-serpent",
        action="store_true",
        help=(
            "leave serpent out, and with it the GCC 16 requirement the "
            "reflected path puts on the C/C++ build"
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    global IMPLEMENTATIONS
    args = parser().parse_args(argv)
    if args.without_serpent:
        IMPLEMENTATIONS = tuple(name for name in IMPLEMENTATIONS if name != "serpent")
    output_dir = DEFAULT_OUTPUT.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    zig = os.environ.get("ZIG", "zig")
    optimize = "ReleaseFast"

    if args.no_build:
        measurements: list[Measurement] = []
        commands: dict[str, list[str]] = {}
        metadata: dict[str, object] = {}
    else:
        parallel_limit = None if args.parallel is None else (args.parallel or machine_threads())
        measurements, commands = run_benchmarks(
            args.runs, parallel_limit, zig, optimize, output_dir, not args.without_serpent
        )
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(ROOT),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "runs": args.runs,
            "parallel": parallel_limit,
            "threads": list(thread_counts(parallel_limit)) if parallel_limit else [1],
            "commands": {
                f"{FORMAT}/{implementation}": command
                for implementation, command in commands.items()
            },
        }

    written: list[Path] = []
    open_pages: list[Path] = []
    for format_name in (FORMAT,):
        format_dir = output_dir / format_name
        format_dir.mkdir(parents=True, exist_ok=True)
        if args.no_build:
            format_summary, runs = load_summary(format_dir / "measurements.json")
            if not any(int(row.get("threads", 1)) > 1 for row in format_summary):
                print(
                    f"note: {format_name} has no thread series in measurements.json; "
                    "run with --parallel to add one",
                    flush=True,
                )
        else:
            runs = args.runs
            format_measurements = [item for item in measurements if item.format == format_name]
            format_summary = aggregate(format_measurements)
            payload = {
                "metadata": metadata | {"format": format_name},
                "measurements": [
                    asdict(item) | {"measured_bytes": item.measured_bytes}
                    for item in format_measurements
                ],
                "summary": format_summary,
            }
            (format_dir / "measurements.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )

        csv_path = format_dir / "summary.csv"
        markdown_path = format_dir / "summary.md"
        page = format_dir / "index.html"
        write_csv(csv_path, format_summary)
        write_markdown(markdown_path, format_summary, runs)
        write_html_page(page, format_summary, runs)
        written.extend((csv_path, markdown_path, page))
        open_pages.append(page)

    for path in written:
        print(f"wrote {path}")
    for page_path in open_pages:
        webbrowser.open(page_path.resolve().as_uri())
        print(f"opened {page_path} in your browser")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"bench.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
