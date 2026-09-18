#pragma once

// The typed schemas the known-data tasks use, mirroring zig/shared.zig field for field and in
// declaration order, so the C++ and Zig implementations encode and decode the same documents.
//
// serpent names a type's fields by reflection, which the annotation opts the type in to; the
// members are then the keys, in this order. Needs GCC 16 with -std=c++26 -freflection.

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include <serpent/json.hpp>

namespace bench::schema {

struct [[= serpent::serializable {}]] small_document {
    std::uint64_t id;
    bool ok;
    std::string name;
    double score;
    std::vector<std::string> tags;
};

using canada_coordinate = std::array<double, 2>;

struct [[= serpent::serializable {}]] canada_properties {
    std::string name;
};

struct [[= serpent::serializable {}]] canada_geometry {
    std::string type;
    std::vector<std::vector<canada_coordinate>> coordinates;
};

struct [[= serpent::serializable {}]] canada_feature {
    std::string type;
    canada_properties properties;
    canada_geometry geometry;
};

struct [[= serpent::serializable {}]] canada_document {
    std::string type;
    std::vector<canada_feature> features;
};

struct [[= serpent::serializable {}]] poem {
    std::string desc;
    std::string name;
    std::string id;
};

struct [[= serpent::serializable {}]] github_actor {
    std::string gravatar_id;
    std::string login;
    std::string avatar_url;
    std::string url;
    std::uint64_t id;
};

struct [[= serpent::serializable {}]] github_repository {
    std::string url;
    std::uint64_t id;
    std::string name;
};

struct [[= serpent::serializable {}]] github_event {
    std::string type;
    std::string created_at;
    github_actor actor;
    github_repository repo;
    // `public` is a keyword here and cannot be a member name, so the key is stated.
    [[= serpent::key("public")]] bool is_public;
    std::string id;
};

struct [[= serpent::serializable {}]] twitter_user {
    std::uint64_t id;
    std::string name;
    std::string screen_name;
    std::string location;
    std::string description;
    bool verified;
    std::uint64_t followers_count;
    std::uint64_t friends_count;
    std::optional<std::uint64_t> statuses_count;
};

struct [[= serpent::serializable {}]] twitter_status {
    std::string created_at;
    std::uint64_t id;
    std::string text;
    twitter_user user;
    std::uint64_t retweet_count;
    std::uint64_t favorite_count;
};

struct [[= serpent::serializable {}]] twitter_document {
    std::vector<twitter_status> statuses;
};

} // namespace bench::schema
