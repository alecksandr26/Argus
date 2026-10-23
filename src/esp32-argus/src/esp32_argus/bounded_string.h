#pragma once

// A string with a fixed capacity, stored inline (no heap). The sender's slots (sender_policy.h:
// held events, outcomes for the Pi) live for the whole run, and keeping their text in std::string
// put up to ~16 x 500 B of long-lived blocks on a heap the Bluetooth stack already crowds (the
// out-of-memory reboot loop of commit f4da937). With these, the slots are static memory sized at
// build time. assign() refuses text that doesn't fit (the string is then left empty and assign()
// returns false), so the caller decides: a too-long id is FAILed `invalid`, a too-long dead-letter
// body is sent as `-`. Pure C++, host-tested in test/host/bounded_string_host_test.cpp.

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

template <size_t N>
class BoundedString {
  static_assert(N >= 2 && N <= 65535, "capacity includes the terminating NUL");

 public:
  static constexpr size_t kMaxLength = N - 1;

  BoundedString() { clear(); }
  BoundedString(const char* text) { assign(text); }          // NOLINT: implicit on purpose
  BoundedString(const std::string& text) { assign(text); }   // NOLINT

  BoundedString& operator=(const char* text) {
    assign(text);
    return *this;
  }
  BoundedString& operator=(const std::string& text) {
    assign(text);
    return *this;
  }

  // False (and empty) when `text` is longer than kMaxLength.
  bool assign(const char* text, size_t length) {
    if (text == nullptr || length > kMaxLength) {
      clear();
      return text == nullptr;
    }
    std::memcpy(buf_, text, length);
    buf_[length] = '\0';
    length_ = (uint16_t)length;
    return true;
  }
  bool assign(const char* text) { return assign(text, text ? std::strlen(text) : 0); }
  bool assign(const std::string& text) { return assign(text.data(), text.size()); }

  // Keeps the first kMaxLength chars of a longer text: for fields where a cut copy still makes
  // sense (a model name, a log/reason token). Returns false when it had to cut.
  bool assignTruncated(const char* text, size_t length) {
    bool fits = length <= kMaxLength;
    assign(text ? text : "", fits ? (text ? length : 0) : kMaxLength);
    return fits;
  }
  bool assignTruncated(const std::string& text) {
    return assignTruncated(text.data(), text.size());
  }

  void clear() {
    buf_[0] = '\0';
    length_ = 0;
  }

  const char* c_str() const { return buf_; }
  size_t size() const { return length_; }
  bool empty() const { return length_ == 0; }
  std::string str() const { return std::string(buf_, length_); }
  operator std::string() const { return str(); }  // NOLINT: reads like the std::string it was

  bool operator==(const char* other) const {
    return other != nullptr && std::strlen(other) == length_ &&
           std::memcmp(buf_, other, length_) == 0;
  }
  bool operator==(const std::string& other) const {
    return other.size() == length_ && std::memcmp(buf_, other.data(), length_) == 0;
  }
  template <size_t M>
  bool operator==(const BoundedString<M>& other) const {
    return other.size() == length_ && std::memcmp(buf_, other.c_str(), length_) == 0;
  }
  bool operator!=(const char* other) const { return !(*this == other); }
  bool operator!=(const std::string& other) const { return !(*this == other); }

 private:
  char buf_[N];
  uint16_t length_;
};

template <size_t N>
bool operator==(const std::string& a, const BoundedString<N>& b) {
  return b == a;
}
template <size_t N>
bool operator==(const char* a, const BoundedString<N>& b) {
  return b == a;
}

template <size_t N>
std::string operator+(const std::string& a, const BoundedString<N>& b) {
  return a + b.str();
}
template <size_t N>
std::string operator+(const char* a, const BoundedString<N>& b) {
  return std::string(a) + b.str();
}

// Capacities (including the NUL). C2.1 allows 1..64 chars for client_event_id, episode and
// alert_type; the Pi's ids are 32 hex chars and its episodes 12.
constexpr size_t kIdCapacity = 65;
using IdString = BoundedString<kIdCapacity>;          // record id, client_event_id, episode
using AlertTypeString = BoundedString<65>;            // the Pi's reason, e.g. "eyes_closed"
using SeverityString = BoundedString<9>;              // "medium" | "critical"
using ModelString = BoundedString<48>;                // ai_metadata.model, informational
using ReasonString = BoundedString<41>;               // a FAIL reason token, e.g. "http_503"
// A dead letter's composed C2 event (the FAIL body): about 300-450 B with this firmware's fields;
// the longest legal one (64-char ids, alert type and a 47-char model) is checked to fit in
// test/host/bounded_string_host_test.cpp.
using EventJsonString = BoundedString<640>;
