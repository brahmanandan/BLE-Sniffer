// proto.h — line-delimited JSON over USB CDC (one object per line, '\n').
//
// All emits must come from the Arduino loop task; BLE/ISR contexts queue their
// data and let loop() serialize it, so lines never interleave.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace proto {

typedef void (*cmd_handler_t)(JsonDocument& cmd);

void begin(cmd_handler_t handler);
void poll();                         // read Serial, dispatch each complete line
void send(JsonDocument& doc);        // serialize + '\n'
void log(const char* level, const char* fmt, ...);

// helpers
void hex(char* out, const uint8_t* data, size_t len, bool spaced = false);
size_t unhex(uint8_t* out, size_t max, const char* in);
uint32_t parseNum(JsonVariantConst v, uint32_t dflt = 0);   // 8, "8", "0x08"

}  // namespace proto
