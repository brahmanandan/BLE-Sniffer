#include "proto.h"

#include <stdarg.h>

namespace proto {

static cmd_handler_t s_handler = nullptr;
static char s_line[1536];
static size_t s_len = 0;
static bool s_overflow = false;

void begin(cmd_handler_t handler) {
  s_handler = handler;
}

static void dispatch() {
  s_line[s_len] = 0;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, s_line, s_len);
  if (err) {
    log("warn", "bad json: %s", err.c_str());
    return;
  }
  if (!doc["cmd"].is<const char*>()) {
    log("warn", "missing cmd");
    return;
  }
  if (s_handler) s_handler(doc);
}

void poll() {
  // Bounded per call so a flood of input cannot starve loop().
  int budget = 512;
  while (budget-- > 0 && Serial.available() > 0) {
    int c = Serial.read();
    if (c < 0) break;
    if (c == '\r') continue;
    if (c == '\n') {
      if (s_overflow) log("warn", "line too long, dropped");
      else if (s_len) dispatch();
      s_len = 0;
      s_overflow = false;
      continue;
    }
    if (s_len < sizeof(s_line) - 1) s_line[s_len++] = (char)c;
    else s_overflow = true;
  }
}

void send(JsonDocument& doc) {
  if (!Serial) return;  // host not listening; don't block
  serializeJson(doc, Serial);
  Serial.write('\n');
}

void log(const char* level, const char* fmt, ...) {
  char msg[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(msg, sizeof(msg), fmt, ap);
  va_end(ap);
  JsonDocument doc;
  doc["ev"] = "log";
  doc["level"] = level;
  doc["msg"] = msg;
  send(doc);
}

void hex(char* out, const uint8_t* data, size_t len, bool spaced) {
  static const char H[] = "0123456789abcdef";
  char* p = out;
  for (size_t i = 0; i < len; i++) {
    if (spaced && i) *p++ = ' ';
    *p++ = H[data[i] >> 4];
    *p++ = H[data[i] & 15];
  }
  *p = 0;
}

static int nib(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

size_t unhex(uint8_t* out, size_t max, const char* in) {
  size_t n = 0;
  int hi = -1;
  for (; in && *in && n < max; in++) {
    int v = nib(*in);
    if (v < 0) continue;  // skip spaces, ':' etc.
    if (hi < 0) hi = v;
    else { out[n++] = (uint8_t)(hi << 4 | v); hi = -1; }
  }
  return n;
}

uint32_t parseNum(JsonVariantConst v, uint32_t dflt) {
  if (v.is<uint32_t>()) return v.as<uint32_t>();
  if (v.is<const char*>()) return strtoul(v.as<const char*>(), nullptr, 0);
  return dflt;
}

}  // namespace proto
