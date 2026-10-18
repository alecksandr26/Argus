// Tiny assertion helpers shared by the *_host_test.cpp files. Each test binary prints one FAIL
// line per failed check and exits non-zero if any failed.
#pragma once
#include <cmath>
#include <cstdio>

static int failures = 0;

#define CHECK(cond)                                               \
  do {                                                            \
    if (!(cond)) {                                                \
      std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      failures++;                                                 \
    }                                                             \
  } while (0)

#define CHECK_NEAR(a, b, eps) CHECK(std::fabs((double)(a) - (double)(b)) <= (eps))

#define RUN(test) test()

static int report(const char* name) {
  if (failures) {
    std::printf("%s: %d check(s) failed\n", name, failures);
    return 1;
  }
  std::printf("%s: all checks passed\n", name);
  return 0;
}
