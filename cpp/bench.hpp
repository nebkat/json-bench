#pragma once

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#ifndef BENCH_DATA_DIR
#define BENCH_DATA_DIR "data/json"
#endif

namespace bench {

inline constexpr std::string_view datasets[] = {
    "canada.json", "citm_catalog.json", "fgo.json", "github_events.json",
    "gsoc-2018.json", "lottie.json", "otfcc.json", "poet.json",
    "twitter.json", "twitterescaped.json",
};

// Every dataset, including the tiny typed-only fixture the DOM tasks skip.
inline constexpr std::string_view all_datasets[] = {
    "canada.json", "citm_catalog.json", "fgo.json", "github_events.json",
    "gsoc-2018.json", "lottie.json", "otfcc.json", "poet.json",
    "twitter.json", "twitterescaped.json", "small.json",
};

// The datasets a typed schema is written for, matching zig/shared.zig.
inline constexpr std::string_view known_datasets[] = {
    "small.json", "canada.json", "github_events.json",
    "poet.json", "twitter.json", "twitterescaped.json",
};

inline bool is_known_dataset(std::string_view name) {
    for (const auto dataset : known_datasets) {
        if (dataset == name) return true;
    }
    return false;
}

inline constexpr std::pair<std::string_view, std::string_view> get_paths[] = {
    {"canada.json", "/features/0/geometry/coordinates/0/0"},
    {"citm_catalog.json", "/areaNames/205705993"},
    {"fgo.json", "/mstSvt/0/relateQuestIds/0"},
    {"github_events.json", "/0/actor/login"},
    {"gsoc-2018.json", "/0/name"},
    {"lottie.json", "/assets/0/layers/0/nm"},
    {"otfcc.json", "/head/version"},
    {"poet.json", "/0/name"},
    {"twitter.json", "/statuses/0/user/id"},
    {"twitterescaped.json", "/statuses/0/user/id"},
};

inline std::string_view get_path(std::string_view name) {
    for (const auto &[dataset, path] : get_paths) {
        if (dataset == name) return path;
    }
    std::cerr << "no get path for " << name << '\n';
    std::exit(1);
}

inline constexpr std::size_t get_batch = 1024;

inline std::size_t repeat_count(std::size_t size) {
    if (size <= 256) return 100000;
    constexpr std::size_t target = 64 * 1024 * 1024;
    if (size == 0 || size >= target) return 1;
    return (target + size - 1) / size;
}

inline std::string read_file(std::string_view name) {
    const auto path = std::filesystem::path(BENCH_DATA_DIR) / name;
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        std::cerr << "failed to read " << path << '\n';
        std::exit(1);
    }
    return {std::istreambuf_iterator<char>(file), {}};
}

template <class T>
inline void black_box(const T &value) {
    asm volatile("" : : "g"(&value) : "memory");
}

inline std::uint64_t now_ns() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        )
            .count()
    );
}

inline void print_result(
    std::string_view parser,
    std::string_view task,
    std::size_t size,
    std::size_t repeats,
    std::uint64_t elapsed_ns,
    std::optional<std::size_t> output_bytes = std::nullopt
) {
    const auto milliseconds = static_cast<double>(elapsed_ns) / static_cast<double>(repeats) / 1'000'000.0;
    const auto mib_per_second =
        static_cast<double>(size) * 1'000'000'000.0 / static_cast<double>(elapsed_ns) * static_cast<double>(repeats) / (1024.0 * 1024.0);
    std::cout << "  " << parser << ' ' << task << ": " << std::fixed << std::setprecision(6) << milliseconds
              << " ms/op, " << std::setprecision(2) << mib_per_second << " MiB/s";
    if (output_bytes) std::cout << " (" << *output_bytes << " bytes)";
    std::cout << '\n';
}

inline void print_dataset(std::string_view name, std::size_t size, std::size_t repeats) {
    std::cout << "\n" << name << " (" << size << " bytes, " << repeats << " repeats)\n";
}

} // namespace bench
