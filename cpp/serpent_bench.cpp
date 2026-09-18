#include "bench.hpp"
#include "serpent_types.hpp"

#include "../c/bench_paths.h"

#include <serpent/json.hpp>
#include <serpent/value.hpp>

#include <cmath>

namespace json = serpent::json;

namespace {

using namespace bench::schema;

[[noreturn]] void fail(std::string_view what, std::string_view dataset) {
    std::cerr << "serpent: " << what << " failed on " << dataset << '\n';
    std::exit(1);
}

const bench_path *find_path(std::string_view name) {
    for (const auto &path : bench_paths) {
        if (name == path.dataset) return &path;
    }
    return nullptr;
}

/// One step of the precompiled path, taken with serpent's own accessors.
json::reader step_into(const json::reader &value, const bench_step &step) {
    if (step.is_index) return value[step.index];
    return value[std::string_view(step.field)];
}

/// Walks the precompiled path from a handle on the document.
///
/// serpent has no DOM to walk: a reader is a handle into the text that parses only what a step
/// asks for, so this resolves the element from the bytes rather than from a prebuilt tree. The
/// timed region therefore covers strictly more than the tree-walking implementations' does.
json::reader walk(const json::reader &root, const bench_path &path) {
    json::reader current = step_into(root, path.steps[0]);
    for (std::size_t i = 1; i < path.count; ++i) {
        json::reader next = step_into(current, path.steps[i]);
        current = std::move(next);
    }
    return current;
}

template <class T>
void known(std::string_view name, const std::string &input, std::size_t repeats) {
    {
        const auto warmup = json::decode<T>(input);
        if (!warmup) fail("known-decode", name);
        bench::black_box(*warmup);
    }

    std::uint64_t elapsed = 0;
    for (std::size_t i = 0; i < repeats; ++i) {
        const auto start = bench::now_ns();
        const auto value = json::decode<T>(input);
        bench::black_box(value);
        const auto end = bench::now_ns();
        if (!value) fail("known-decode", name);
        elapsed += std::max<std::uint64_t>(1, end - start);
    }
    bench::print_result("serpent", "known-decode", input.size(), repeats, elapsed);

    const auto fixture = json::decode<T>(input);
    if (!fixture) fail("known-encode fixture", name);
    const auto warmup = json::encode(*fixture);

    elapsed = 0;
    for (std::size_t i = 0; i < repeats; ++i) {
        const auto start = bench::now_ns();
        const auto output = json::encode(*fixture);
        bench::black_box(output);
        const auto end = bench::now_ns();
        elapsed += std::max<std::uint64_t>(1, end - start);
    }
    bench::print_result("serpent", "known-encode", warmup.size(), repeats, elapsed, warmup.size());
}

void known_tasks(std::string_view name, const std::string &input, std::size_t repeats) {
    if (name == "small.json") return known<small_document>(name, input, repeats);
    if (name == "canada.json") return known<canada_document>(name, input, repeats);
    if (name == "github_events.json") return known<std::vector<github_event>>(name, input, repeats);
    if (name == "poet.json") return known<std::vector<poem>>(name, input, repeats);
    return known<twitter_document>(name, input, repeats);
}

void arbitrary_tasks(std::string_view name, const std::string &input, std::size_t repeats) {
    {
        const auto warmup = json::decode<serpent::value>(input);
        if (!warmup) fail("arbitrary-decode", name);
        bench::black_box(*warmup);
    }

    std::uint64_t elapsed = 0;
    for (std::size_t i = 0; i < repeats; ++i) {
        const auto start = bench::now_ns();
        const auto value = json::decode<serpent::value>(input);
        bench::black_box(value);
        const auto end = bench::now_ns();
        if (!value) fail("arbitrary-decode", name);
        elapsed += std::max<std::uint64_t>(1, end - start);
    }
    bench::print_result("serpent", "arbitrary-decode", input.size(), repeats, elapsed);

    elapsed = 0;
    for (std::size_t i = 0; i < repeats; ++i) {
        const auto start = bench::now_ns();
        const auto value = json::decode<serpent::value>(input);
        if (!value) fail("transform", name);
        const auto output = json::encode(*value);
        bench::black_box(output);
        const auto end = bench::now_ns();
        elapsed += std::max<std::uint64_t>(1, end - start);
    }
    bench::print_result("serpent", "transform", input.size(), repeats, elapsed);

    const auto *path = find_path(name);
    if (path == nullptr) fail("get path", name);
    const auto document = json::reader::over(input);
    {
        const auto found = walk(document, *path);
        if (found.type() == serpent::kind::invalid) fail("get", name);
        bench::black_box(found);
    }

    elapsed = 0;
    for (std::size_t i = 0; i < repeats; ++i) {
        const auto start = bench::now_ns();
        for (std::size_t b = 0; b < bench::get_batch; ++b) bench::black_box(walk(document, *path));
        const auto end = bench::now_ns();
        elapsed += std::max<std::uint64_t>(1, end - start);
    }
    bench::print_result("serpent", "get", input.size(), repeats * bench::get_batch, elapsed);
}

// --- correctness ----------------------------------------------------------------------------
//
// The harness only times; nothing in it compares outputs. `--verify` is that missing check: for
// every dataset the typed value, the tree and the resolved element all have to come back as the
// data says they are. The expected values are read out of the corpus with an unrelated parser
// (python3's json module) and written here as literals, so agreement is not serpent agreeing
// with itself.

int failures = 0;

void check(bool condition, std::string_view dataset, std::string_view what) {
    if (condition) return;
    std::cerr << "  FAIL " << dataset << ": " << what << '\n';
    failures++;
}

/// Encoding, decoding what was encoded, and encoding that must give the same text back.
template <class T>
void check_typed_round_trip(std::string_view name, const T &value) {
    const auto text = json::encode(value);
    const auto again = json::decode<T>(text);
    check(again.has_value(), name, "typed round-trip does not decode");
    if (!again) return;
    check(json::encode(*again) == text, name, "typed round-trip is not stable");
}

void check_tree(std::string_view name, const std::string &input) {
    const auto tree = json::decode<serpent::value>(input);
    check(tree.has_value(), name, "tree does not decode");
    if (!tree) return;
    const auto text = json::encode(*tree);
    const auto again = json::decode<serpent::value>(text);
    check(again.has_value(), name, "encoded tree does not decode");
    if (!again) return;
    check(*again == *tree, name, "tree round-trip loses data");
    check(json::encode(*again) == text, name, "tree round-trip is not stable");
}

bool near(double value, double expected) { return std::abs(value - expected) <= 1e-9 * std::abs(expected); }

void verify_known(std::string_view name, const std::string &input) {
    if (name == "small.json") {
        const auto value = json::decode<small_document>(input);
        check(value.has_value(), name, "typed decode");
        if (!value) return;
        check(value->id == 42 && value->ok && value->name == "jsonz" && near(value->score, 3.5), name, "scalars");
        check(value->tags == std::vector<std::string> { "zig", "json" }, name, "tags");
        check_typed_round_trip(name, *value);
    } else if (name == "canada.json") {
        const auto value = json::decode<canada_document>(input);
        check(value.has_value(), name, "typed decode");
        if (!value) return;
        check(value->type == "FeatureCollection" && value->features.size() == 1, name, "root");
        const auto &feature = value->features.front();
        check(feature.properties.name == "Canada" && feature.geometry.type == "Polygon", name, "feature");
        const auto &rings = feature.geometry.coordinates;
        std::size_t points = 0;
        for (const auto &ring : rings) points += ring.size();
        check(rings.size() == 480 && points == 55563, name, "coordinate counts");
        check(near(rings.front().front()[0], -65.61361699999998) && near(rings.front().front()[1], 43.42027300000001),
              name, "first coordinate");
        check(near(rings.back().back()[0], -70.11193799999995) && near(rings.back().back()[1], 83.10942100000011),
              name, "last coordinate");
        check_typed_round_trip(name, *value);
    } else if (name == "github_events.json") {
        const auto value = json::decode<std::vector<github_event>>(input);
        check(value.has_value(), name, "typed decode");
        if (!value) return;
        check(value->size() == 30, name, "event count");
        const auto &event = value->front();
        check(event.type == "PushEvent" && event.id == "1652857722" && event.is_public, name, "event");
        check(event.actor.login == "jathanism" && event.actor.id == 138052, name, "actor");
        check(event.repo.name == "jathanism/trigger", name, "repo");
        check_typed_round_trip(name, *value);
    } else if (name == "poet.json") {
        const auto value = json::decode<std::vector<poem>>(input);
        check(value.has_value(), name, "typed decode");
        if (!value) return;
        check(value->size() == 8934, name, "poem count");
        check(value->front().name == "宋太祖", name, "first name");
        check(value->front().id == "3db53cab-f710-458e-93c0-e36a15142ec7", name, "first id");
        check(value->back().name == "方鴻飛", name, "last name");
        check_typed_round_trip(name, *value);
    } else {
        const auto value = json::decode<twitter_document>(input);
        check(value.has_value(), name, "typed decode");
        if (!value) return;
        check(value->statuses.size() == 100, name, "status count");
        const auto &status = value->statuses.front();
        check(status.id == 505874924095815700ULL, name, "status id");
        check(status.retweet_count == 0 && status.favorite_count == 0, name, "counts");
        check(status.user.id == 1186275104 && status.user.screen_name == "ayuu0123", name, "user");
        check(status.user.followers_count == 262, name, "followers");
        check(status.user.statuses_count == 1769, name, "statuses_count");
        check_typed_round_trip(name, *value);
    }
}

void verify_get(std::string_view name, const std::string &input) {
    const auto *path = find_path(name);
    if (path == nullptr) return;
    const auto document = json::reader::over(input);
    const auto found = walk(document, *path);

    if (name == "canada.json") {
        check(near(found[std::size_t { 0 }].as<double>().value_or(0), -65.61361699999998)
                  && near(found[std::size_t { 1 }].as<double>().value_or(0), 43.42027300000001),
              name, "get element");
    } else if (name == "citm_catalog.json") {
        check(found.as<std::string>() == "Arrière-scène central", name, "get element");
    } else if (name == "fgo.json") {
        check(found.as<std::int64_t>() == 91100101, name, "get element");
    } else if (name == "github_events.json") {
        check(found.as<std::string>() == "jathanism", name, "get element");
    } else if (name == "gsoc-2018.json") {
        check(found.as<std::string>() == "Instructor Interface for Plagiarism Detection", name, "get element");
    } else if (name == "lottie.json") {
        check(found.as<std::string>() == "Shape Layer 52", name, "get element");
    } else if (name == "otfcc.json") {
        check(near(found.as<double>().value_or(0), 1.0), name, "get element");
    } else if (name == "poet.json") {
        check(found.as<std::string>() == "宋太祖", name, "get element");
    } else {
        check(found.as<std::int64_t>() == 1186275104, name, "get element");
    }
}

int verify() {
    std::cout << "serpent correctness check\n";
    for (const auto name : bench::all_datasets) {
        const auto input = bench::read_file(name);
        if (bench::is_known_dataset(name)) verify_known(name, input);
        if (name != "small.json") {
            check_tree(name, input);
            verify_get(name, input);
        }
        std::cout << "  " << name << '\n';
    }
    if (failures != 0) {
        std::cout << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all checks passed\n";
    return 0;
}

} // namespace

int main(int argc, char **argv) {
    if (argc > 1 && std::string_view(argv[1]) == "--verify") return verify();

    std::cout << "serpent benchmark\n";
    std::cout << "data: data/json, input read and cleanup excluded\n";

    for (const auto name : bench::all_datasets) {
        const auto input = bench::read_file(name);
        const auto repeats = bench::repeat_count(input.size());
        bench::print_dataset(name, input.size(), repeats);

        if (bench::is_known_dataset(name)) known_tasks(name, input, repeats);
        if (name != "small.json") arbitrary_tasks(name, input, repeats);
    }
}
