cmake_minimum_required(VERSION 3.24)
project(json_bench LANGUAGES C CXX)

include(FetchContent)

FetchContent_Declare(
    simdjson
    URL https://github.com/simdjson/simdjson/archive/refs/tags/v4.6.11.tar.gz
)
FetchContent_Declare(
    glaze
    URL https://github.com/stephenberry/glaze/archive/refs/tags/v8.3.0.tar.gz
)
FetchContent_Declare(
    yyjson
    URL https://github.com/ibireme/yyjson/archive/refs/tags/0.13.0.tar.gz
    SOURCE_SUBDIR do-not-build
)

# serpent is a private repository, so this fetches over SSH and a machine without access to it
# cannot build the target. Point FETCHCONTENT_SOURCE_DIR_SERPENT at a checkout to build from one:
#
#   cmake --preset release -DFETCHCONTENT_SOURCE_DIR_SERPENT=/path/to/cpp-serpent
#
# Its reflected path - the one measured here - needs GCC 16 with -std=c++26 -freflection, so
# serpent_bench is the one target built to a different standard with an extra flag.
option(JSON_BENCH_SERPENT "Build the serpent benchmark (needs GCC 16)" ON)
if(JSON_BENCH_SERPENT)
    if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR CMAKE_CXX_COMPILER_VERSION VERSION_LESS 16)
        message(FATAL_ERROR
            "serpent names a type's fields by reflection, which today needs GCC 16 with "
            "-std=c++26 -freflection; this is ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}. "
            "Configure with -DCMAKE_CXX_COMPILER=g++-16 -DCMAKE_C_COMPILER=gcc-16, "
            "or with -DJSON_BENCH_SERPENT=OFF to leave serpent out.")
    endif()
    FetchContent_Declare(
        serpent
        GIT_REPOSITORY git@github.com:nebkat/cpp-serpent.git
        GIT_TAG 0d4eb84ef48a134817c8d13410280ab53a8376aa
    )
endif()

FetchContent_MakeAvailable(simdjson glaze yyjson)
if(JSON_BENCH_SERPENT)
    FetchContent_MakeAvailable(serpent)
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(yyjson_bench c/yyjson_bench.c ${yyjson_SOURCE_DIR}/src/yyjson.c)
target_include_directories(yyjson_bench PRIVATE ${yyjson_SOURCE_DIR}/src)
target_link_libraries(yyjson_bench PRIVATE m)

add_executable(simdjson_bench cpp/simdjson_bench.cpp)
target_include_directories(simdjson_bench PRIVATE cpp)
target_link_libraries(simdjson_bench PRIVATE simdjson)

add_executable(glaze_bench cpp/glaze_bench.cpp)
target_include_directories(glaze_bench PRIVATE cpp ${glaze_SOURCE_DIR}/include)

set(BENCH_TARGETS yyjson_bench simdjson_bench glaze_bench)

if(JSON_BENCH_SERPENT)
    add_executable(serpent_bench cpp/serpent_bench.cpp)
    target_include_directories(serpent_bench PRIVATE cpp)
    target_link_libraries(serpent_bench PRIVATE serpent::serpent)
    set_target_properties(serpent_bench PROPERTIES
        CXX_STANDARD 26
        CXX_STANDARD_REQUIRED ON
        CXX_EXTENSIONS OFF)
    target_compile_options(serpent_bench PRIVATE -freflection)
    target_link_options(serpent_bench PRIVATE -freflection)
    list(APPEND BENCH_TARGETS serpent_bench)
endif()

foreach(target ${BENCH_TARGETS})
    target_compile_options(${target} PRIVATE $<$<CONFIG:Release>:-O3;-march=native>)
endforeach()
