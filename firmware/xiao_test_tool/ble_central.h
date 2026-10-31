// ble_central.h — BLE HID central: scan, connect, bond, raw GATT discovery,
// HID report decode -> key events, ATV voice forwarding, remote battery.
//
// GATT is driven directly through the SoftDevice (sd_ble_gattc_*) with responses
// captured by Bluefruit.setEventCallback(); this gives a full GATT dump and the
// HID Report Reference descriptors, which Bluefruit's client classes don't expose.
#pragma once

#include <Arduino.h>

namespace blec {

void begin();
void poll();                                   // call from loop()

bool scan(uint16_t sec);                       // 0 = until stopped
void scanStop();
bool connect(const char* addr, const char* atype);
bool bond();
bool pair(const char* addr, const char* atype);  // connect + pair + subscribe, with retries
void lock(const char* addr);                     // auto-reconnect only to this remote ("" = unlock)
void sendBonds();                                // emit the `bonds` list
void bondDelete(const char* addr);
bool repair();                                // re-run pairing on the live link (diagnostic)
void disconnect();
void subscribe(const char* what);              // hid | voice | battery | all
void readRemoteBattery();
void mic(bool open);
bool gattWrite(uint16_t handle, const uint8_t* data, uint16_t len, bool with_rsp);
bool gattRead(uint16_t handle);
void clearBonds();
void setEnabled(bool on);
void sendState();
void sendInfo();                              // gatt + hid + state for a (re)attached host

bool connected();
uint8_t bondCount();

}  // namespace blec
