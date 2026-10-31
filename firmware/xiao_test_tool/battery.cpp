#include "battery.h"

namespace battery {

// (1M + 510k) / 510k, from the XIAO nRF52840 schematic.
static const float DIVIDER = 1510.0f / 510.0f;

void begin() {
  pinMode(VBAT_ENABLE, OUTPUT);
  digitalWrite(VBAT_ENABLE, LOW);
}

uint16_t readMv() {
  analogReference(AR_INTERNAL);      // 0.6 V * 6 = 3.6 V full scale
  analogReadResolution(12);
  analogSampleTime(40);              // 1 MΩ source impedance needs the long acquisition
  analogRead(PIN_VBAT);              // discard first sample after reconfiguration
  uint32_t acc = 0;
  for (int i = 0; i < 8; i++) acc += analogRead(PIN_VBAT);
  float pin_mv = (acc / 8.0f) * 3600.0f / 4095.0f;
  analogReference(AR_DEFAULT);
  analogSampleTime(3);
  return (uint16_t)(pin_mv * DIVIDER + 0.5f);
}

uint8_t percent(uint16_t mv) {
  // Typical 1-cell LiPo resting curve (mV, %).
  static const uint16_t C[][2] = {
    {4200, 100}, {4100, 90}, {4000, 80}, {3900, 65}, {3800, 50},
    {3750, 40}, {3700, 30}, {3650, 20}, {3600, 12}, {3500, 5}, {3300, 0},
  };
  if (mv >= C[0][0]) return 100;
  for (unsigned i = 1; i < sizeof(C) / sizeof(C[0]); i++) {
    if (mv >= C[i][0]) {
      uint16_t v0 = C[i][0], v1 = C[i - 1][0], p0 = C[i][1], p1 = C[i - 1][1];
      return p0 + (uint32_t)(mv - v0) * (p1 - p0) / (v1 - v0);
    }
  }
  return 0;
}

}  // namespace battery
