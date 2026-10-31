// ir.h — M5Stack Unit IR (U002): RX on D0/P0.02 (demodulated, active-low),
// TX on D1/P0.03 (38 kHz carrier made here with HardwarePWM).
#pragma once

#include <Arduino.h>

namespace ir {

static const uint16_t MAX_EDGES = 200;

struct Frame {
  uint16_t n;                 // number of mark/space durations (starts with a mark)
  uint16_t t[MAX_EDGES];      // µs
};

void begin(uint8_t rx_pin, uint8_t tx_pin);
void setRx(bool on);
bool rxEnabled();
void poll();                                  // emits {"ev":"ir",...} for finished frames

bool sendNEC(uint32_t addr, uint8_t cmd, uint8_t repeats = 0);
bool sendSIRC(uint16_t addr, uint8_t cmd, uint8_t bits = 12);
bool sendRaw(uint32_t carrier_hz, const uint16_t* t, uint16_t n);

}  // namespace ir
