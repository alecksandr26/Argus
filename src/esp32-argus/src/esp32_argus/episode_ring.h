#pragma once

// The last N episode ids seen, oldest overwritten first: "has this episode already sounded / had
// its SMS?" (grip-alarm contracts C4, C5). A record the Pi serves again after its lease lapsed,
// or a later critical of the same episode, carries the same episode id, so remembering a handful
// is enough. Static storage (bounded_string.h), pure, header-only.

#include <string>

#include "bounded_string.h"

template <int N>
class EpisodeRing {
  static_assert(N >= 1, "remember at least one episode");

 public:
  // An empty id is never "seen" (and never stored): there is nothing to dedupe it by.
  bool contains(const std::string& episode) const {
    if (episode.empty()) return false;
    for (int i = 0; i < count_; i++) {
      if (ids_[i] == episode) return true;
    }
    return false;
  }

  // Remembers `episode` (no-op if it is already there); the oldest falls off when full. An empty
  // id, or one longer than C2.1's 64 chars (never taken: pull_session FAILs it invalid), is not
  // stored.
  void add(const std::string& episode) {
    if (episode.empty() || episode.size() > IdString::kMaxLength || contains(episode)) return;
    ids_[next_] = episode;
    next_ = (next_ + 1) % N;
    if (count_ < N) count_++;
  }

  int size() const { return count_; }

 private:
  IdString ids_[N];
  int next_ = 0;
  int count_ = 0;
};
