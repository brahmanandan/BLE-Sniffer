// xiao_test_tool — XIAO nRF52840 bench firmware for TV-remote testing.
//
//   BLE HID central (bonds with the remote, decodes key presses, forwards voice)
//   IR transceiver on an M5Stack Unit IR (RX D0/P0.02, TX D1/P0.03)
//   Jig battery telemetry (VBAT on P0.31)
//
// Host protocol: one JSON object per line over USB CDC (see OPENCODE brief §6.1).
// Board: Seeed XIAO nRF52840 (Seeed nRF52 Boards core, Adafruit Bluefruit stack).

#include <Arduino.h>
#include <bluefruit.h>

#include "battery.h"
#include "ble_central.h"
#include "ir.h"
#include "proto.h"

#define FW_NAME "xiao-test-tool"
#define FW_VER  "0.3"

static const uint8_t PIN_IR_RX = D0;
static const uint8_t PIN_IR_TX = D1;
static const uint32_t BATTERY_PERIOD_MS = 30000;
// Soft limits only (brief §12): the cell's own PCM is the real over-discharge cut-off.
static const uint16_t BATT_WARN_MV = 3400;    // flag "low" to the app
static const uint16_t BATT_CUT_MV = 3300;     // refuse IR TX (the heaviest load) below this
static const uint16_t BATT_NO_CELL_MV = 2500; // below this the reading is no cell / USB only: don't cut

static uint32_t s_last_batt = 0;
static bool s_ble_on = true, s_ir_on = true;
static bool s_batt_low = false, s_batt_cut = false;

static void sendHello() {
  JsonDocument doc;
  doc["ev"] = "hello";
  doc["fw"] = FW_NAME;
  doc["ver"] = FW_VER;
  JsonArray roles = doc["roles"].to<JsonArray>();
  roles.add("ble_central");
  roles.add("ir");
  roles.add("battery");
  ble_gap_addr_t own = Bluefruit.getAddr();
  char a[18];
  sprintf(a, "%02X:%02X:%02X:%02X:%02X:%02X", own.addr[5], own.addr[4], own.addr[3], own.addr[2], own.addr[1], own.addr[0]);
  doc["addr"] = a;
  doc["bonds"] = blec::bondCount();
  doc["mode"] = s_ble_on && s_ir_on ? "both" : s_ble_on ? "ble" : "ir";
  proto::send(doc);
}

static void sendBattery() {
  uint16_t mv = battery::readMv();
  bool cell = mv >= BATT_NO_CELL_MV;
  // 100 mV hysteresis so a load step doesn't make the flags flicker.
  bool low = cell && mv < (s_batt_low ? BATT_WARN_MV + 100 : BATT_WARN_MV);
  bool cut = cell && mv < (s_batt_cut ? BATT_CUT_MV + 100 : BATT_CUT_MV);
  if (low && !s_batt_low) proto::log("warn", "jig battery low (%u mV): charge over USB-C", mv);
  if (cut && !s_batt_cut) proto::log("warn", "jig battery below %u mV: IR TX disabled until charged", BATT_CUT_MV);
  if (!cut && s_batt_cut) proto::log("info", "jig battery recovered (%u mV): IR TX enabled", mv);
  s_batt_low = low;
  s_batt_cut = cut;
  JsonDocument doc;
  doc["ev"] = "battery";
  doc["which"] = "jig";
  doc["mv"] = mv;
  doc["pct"] = battery::percent(mv);
  doc["low"] = low;
  doc["cut"] = cut;
  proto::send(doc);
  s_last_batt = millis();
}

static bool irTxAllowed() {
  if (!s_batt_cut) return true;
  proto::log("error", "ir_tx refused: jig battery below %u mV", BATT_CUT_MV);
  JsonDocument doc;
  doc["ev"] = "ir_tx_done";
  doc["ok"] = false;
  doc["err"] = "battery low";
  proto::send(doc);
  return false;
}

static void onCommand(JsonDocument& cmd) {
  const char* c = cmd["cmd"];
  if (!strcmp(c, "ping")) {
    sendHello();
  } else if (!strcmp(c, "scan")) {
    blec::scan(cmd["sec"] | 6);
  } else if (!strcmp(c, "scan_stop")) {
    blec::scanStop();
  } else if (!strcmp(c, "connect")) {
    blec::connect(cmd["addr"], cmd["atype"] | "random");
  } else if (!strcmp(c, "bond")) {
    blec::bond();
  } else if (!strcmp(c, "pair")) {
    blec::pair(cmd["addr"] | "", cmd["atype"] | "random");
  } else if (!strcmp(c, "lock")) {
    blec::lock(cmd["addr"] | "");
  } else if (!strcmp(c, "unlock")) {
    blec::lock("");
  } else if (!strcmp(c, "bonds")) {
    blec::sendBonds();
  } else if (!strcmp(c, "bond_delete")) {
    blec::bondDelete(cmd["addr"] | "");
  } else if (!strcmp(c, "repair")) {
    blec::repair();
  } else if (!strcmp(c, "disconnect")) {
    blec::disconnect();
  } else if (!strcmp(c, "subscribe")) {
    blec::subscribe(cmd["what"] | "all");
  } else if (!strcmp(c, "battery")) {
    sendBattery();
    blec::readRemoteBattery();
  } else if (!strcmp(c, "state")) {
    blec::sendState();
  } else if (!strcmp(c, "info")) {
    blec::sendInfo();
  } else if (!strcmp(c, "mic")) {
    blec::mic(cmd["on"] | true);
  } else if (!strcmp(c, "gatt_write")) {
    uint8_t buf[244];
    size_t n = proto::unhex(buf, sizeof buf, cmd["bytes"] | "");
    blec::gattWrite(proto::parseNum(cmd["handle"]), buf, n, cmd["rsp"] | true);
  } else if (!strcmp(c, "gatt_read")) {
    blec::gattRead(proto::parseNum(cmd["handle"]));
  } else if (!strcmp(c, "clear_bonds")) {
    blec::clearBonds();
  } else if (!strcmp(c, "ir_rx")) {
    ir::setRx(cmd["on"] | true);
    proto::log("info", "ir rx %s", ir::rxEnabled() ? "on" : "off");
  } else if (!strcmp(c, "ir_tx")) {
    if (!irTxAllowed()) return;
    const char* p = cmd["proto"] | "NEC";
    uint32_t addr = proto::parseNum(cmd["addr"]);
    uint8_t code = proto::parseNum(cmd["code"]);
    if (!strcasecmp(p, "NEC")) ir::sendNEC(addr, code, cmd["repeats"] | 0);
    else if (!strcasecmp(p, "SIRC")) ir::sendSIRC(addr, code, cmd["bits"] | 12);
    else proto::log("error", "ir_tx: unsupported proto %s (use ir_tx_raw)", p);
  } else if (!strcmp(c, "ir_tx_raw")) {
    if (!irTxAllowed()) return;
    static uint16_t t[ir::MAX_EDGES];
    uint16_t n = 0;
    for (JsonVariant v : cmd["timings"].as<JsonArray>())
      if (n < ir::MAX_EDGES) t[n++] = v.as<uint16_t>();
    ir::sendRaw(cmd["carrier"] | 38000, t, n);
  } else if (!strcmp(c, "mode")) {
    const char* m = cmd["value"] | "both";
    s_ble_on = strcmp(m, "ir") != 0;
    s_ir_on = strcmp(m, "ble") != 0;
    blec::setEnabled(s_ble_on);
    ir::setRx(s_ir_on);
    sendHello();
  } else if (!strcmp(c, "dfu")) {
    // Reboot into the UF2 bootloader (XIAO-BOOT drive) so the host can reflash.
    proto::log("info", "entering UF2 bootloader");
    Serial.flush();
    delay(100);
    enterUf2Dfu();
  } else {
    proto::log("warn", "unknown cmd %s", c);
  }
}

void setup() {
  Serial.begin(1000000);
  battery::begin();
  blec::begin();
  ir::begin(PIN_IR_RX, PIN_IR_TX);
  proto::begin(onCommand);
  pinMode(LED_BLUE, OUTPUT);
  digitalWrite(LED_BLUE, HIGH);  // XIAO RGB LED is active-low
  // Give the host a moment to open the port so it sees the boot hello.
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 1500) delay(10);
  sendHello();
  sendBattery();
}

void loop() {
  proto::poll();
  blec::poll();
  if (s_ir_on) ir::poll();
  if (millis() - s_last_batt > BATTERY_PERIOD_MS) sendBattery();
  digitalWrite(LED_BLUE, blec::connected() ? LOW : HIGH);
  delay(1);
}
