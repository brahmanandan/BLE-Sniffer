// battery.h — jig LiPo voltage on the XIAO nRF52840.
//
// VBAT -> 1M -> P0.31 (PIN_VBAT) -> 510k -> P0.14 (VBAT_ENABLE).
// Driving P0.14 LOW closes the divider so P0.31 sees VBAT * 510/1510.
//
// P0.14 is kept LOW permanently (costs ~2.8 µA). Releasing it would let P0.31
// float up to the full battery voltage, which exceeds the nRF52840's VDD-limited
// input range (Seeed's own guidance is to leave VBAT_ENABLE low).
#pragma once

#include <Arduino.h>

namespace battery {

void begin();
uint16_t readMv();
uint8_t percent(uint16_t mv);

}  // namespace battery
