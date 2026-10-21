#include "panic_latch.h"

#include <cstdio>
#include <cstdlib>

namespace {
const char kMagic[] = "PQ1";

// Splits on `sep`, keeping empty fields.
int split(const std::string& s, char sep, std::string* out, int max) {
  int n = 0;
  size_t start = 0;
  while (n < max) {
    size_t at = s.find(sep, start);
    out[n++] = s.substr(start, at == std::string::npos ? std::string::npos : at - start);
    if (at == std::string::npos) break;
    start = at + 1;
  }
  return n;
}

bool safeToken(const std::string& s) {
  return s.find(';') == std::string::npos && s.find(',') == std::string::npos;
}
}  // namespace

void PanicQueue::push(const PanicEvent& event) {
  if (count_ == kCapacity) {  // full: drop the oldest press
    head_ = (head_ + 1) % kCapacity;
    count_--;
  }
  events_[(head_ + count_) % kCapacity] = event;
  count_++;
}

void PanicQueue::markFrontPosted() {
  if (count_ == 0) return;
  head_ = (head_ + 1) % kCapacity;
  count_--;
}

std::string PanicQueue::serialize() const {
  std::string out = kMagic;
  char buf[96];
  for (int i = 0; i < count_; i++) {
    const PanicEvent& e = events_[(head_ + i) % kCapacity];
    snprintf(buf, sizeof(buf), ";%lu,%d,%.7f,%.7f,", (unsigned long)e.atMs, e.hasFix ? 1 : 0,
             e.lat, e.lon);
    out += buf;
    out += (safeToken(e.occurredAt) ? e.occurredAt : "") + "," +
           (safeToken(e.clientEventId) ? e.clientEventId : "");
  }
  return out;
}

bool PanicQueue::deserialize(const std::string& blob) {
  head_ = 0;
  count_ = 0;
  std::string records[kCapacity + 1];
  int n = split(blob, ';', records, kCapacity + 1);
  if (n < 1 || records[0] != kMagic) return false;
  for (int i = 1; i < n; i++) {
    std::string f[6];
    if (split(records[i], ',', f, 6) != 6) {
      count_ = 0;
      return false;
    }
    PanicEvent e;
    e.atMs = (uint32_t)strtoul(f[0].c_str(), nullptr, 10);
    e.hasFix = f[1] == "1";
    e.lat = strtod(f[2].c_str(), nullptr);
    e.lon = strtod(f[3].c_str(), nullptr);
    e.occurredAt = f[4];
    e.clientEventId = f[5];
    push(e);
  }
  return true;
}
