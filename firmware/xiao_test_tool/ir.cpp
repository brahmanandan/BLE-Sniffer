#include "ir.h"

#include "proto.h"

namespace ir {

// End-of-frame: no edge for this long. Longest intra-frame gap is NEC's 4.5 ms
// leader space; NEC repeats arrive ~40 ms after a frame, so they stay separate.
static const uint32_t FRAME_GAP_US = 10000;

static uint8_t s_rx_pin = 0xFF, s_tx_pin = 0xFF;
static volatile bool s_rx_on = false;

// ISR capture buffer
static volatile uint16_t s_n = 0;
static volatile uint32_t s_last = 0;
static volatile bool s_started = false;
static uint16_t s_buf[MAX_EDGES];

static Frame s_frame;               // copy handed to the decoder
static uint32_t s_last_nec_addr = 0;
static uint8_t s_last_nec_cmd = 0;
static bool s_have_nec = false;
static volatile bool s_tx_busy = false;

static HardwarePWM& PWM = HwPWM3;   // HwPWM0.. are taken by analogWrite()
static uint16_t s_pwm_top = 421;    // 16 MHz / 38 kHz

static void isr() {
  uint32_t now = micros();
  if (!s_started) {
    // First edge of a frame must be the falling edge into a mark.
    if (digitalRead(s_rx_pin) != LOW) return;
    s_started = true;
    s_n = 0;
  } else if (s_n < MAX_EDGES) {
    uint32_t d = now - s_last;
    s_buf[s_n++] = d > 0xFFFF ? 0xFFFF : (uint16_t)d;
  }
  s_last = now;
}

void begin(uint8_t rx_pin, uint8_t tx_pin) {
  s_rx_pin = rx_pin;
  s_tx_pin = tx_pin;
  pinMode(s_rx_pin, INPUT_PULLUP);
  pinMode(s_tx_pin, OUTPUT);
  digitalWrite(s_tx_pin, LOW);
  PWM.setClockDiv(PWM_PRESCALER_PRESCALER_DIV_1);
  PWM.setMaxValue(s_pwm_top);
  PWM.addPin(s_tx_pin);
  PWM.writePin(s_tx_pin, 0);
  setRx(true);
}

void setRx(bool on) {
  if (on == s_rx_on) return;
  s_rx_on = on;
  if (on) attachInterrupt(digitalPinToInterrupt(s_rx_pin), isr, CHANGE);
  else detachInterrupt(digitalPinToInterrupt(s_rx_pin));
  s_started = false;
}

bool rxEnabled() { return s_rx_on; }

// ---------------------------------------------------------------------------
// Decoders
// ---------------------------------------------------------------------------
static bool near(uint32_t v, uint32_t want, uint8_t pct = 30) {
  uint32_t tol = want * pct / 100;
  return v + tol >= want && v <= want + tol;
}

static bool decodeNEC(const Frame& f, uint32_t& addr, uint8_t& cmd, bool& rpt) {
  if (f.n >= 3 && near(f.t[0], 9000, 25) && near(f.t[1], 2250, 25) && f.n <= 4) {
    rpt = true;
    return s_have_nec;
  }
  if (f.n < 67 || !near(f.t[0], 9000, 25) || !near(f.t[1], 4500, 25)) return false;
  uint32_t v = 0;
  for (int b = 0; b < 32; b++) {
    uint16_t mark = f.t[2 + 2 * b], space = f.t[3 + 2 * b];
    if (!near(mark, 560, 50)) return false;
    if (near(space, 1690, 35)) v |= 1UL << b;
    else if (!near(space, 560, 50)) return false;
  }
  uint8_t a0 = v, a1 = v >> 8, c0 = v >> 16, c1 = v >> 24;
  if ((uint8_t)~c0 != c1) return false;
  addr = ((uint8_t)~a0 == a1) ? a0 : (uint32_t)a0 | (uint32_t)a1 << 8;  // extended NEC
  cmd = c0;
  rpt = false;
  return true;
}

static bool decodeSIRC(const Frame& f, uint16_t& addr, uint8_t& cmd, uint8_t& nbits) {
  if (f.n < 25 || !near(f.t[0], 2400, 25) || !near(f.t[1], 600, 40)) return false;
  uint32_t v = 0;
  uint8_t b = 0;
  for (uint16_t i = 2; i < f.n && b < 20; i += 2, b++) {
    if (near(f.t[i], 1200, 30)) v |= 1UL << b;
    else if (!near(f.t[i], 600, 40)) return false;
  }
  if (b != 12 && b != 15 && b != 20) return false;
  nbits = b;
  cmd = v & 0x7F;
  addr = v >> 7;
  return true;
}

static bool decodeRC5(const Frame& f, uint16_t& addr, uint8_t& cmd, bool& toggle) {
  const uint16_t T = 889;
  uint8_t half[64];
  uint8_t nh = 0;
  half[nh++] = 0;  // first half of the start bit is idle
  for (uint16_t i = 0; i < f.n && nh < 62; i++) {
    uint8_t lvl = (i & 1) ? 0 : 1;   // even index = mark
    uint8_t k;
    if (near(f.t[i], T, 35)) k = 1;
    else if (near(f.t[i], 2 * T, 25)) k = 2;
    else return false;
    while (k-- && nh < 62) half[nh++] = lvl;
  }
  if (nh & 1) half[nh++] = 0;
  if (nh != 28) return false;
  uint16_t v = 0;
  for (uint8_t b = 0; b < 14; b++) {
    uint8_t a = half[2 * b], c = half[2 * b + 1];
    if (a == c) return false;
    v = v << 1 | (c ? 1 : 0);
  }
  if (!(v & 0x2000)) return false;           // S1 must be 1
  toggle = v & 0x0800;
  addr = (v >> 6) & 0x1F;
  cmd = (v & 0x3F) | ((v & 0x1000) ? 0 : 0x40);  // S2 inverted = command bit 6 (RC5X)
  return true;
}

static void emitFrame(const Frame& f) {
  JsonDocument doc;
  doc["ev"] = "ir";
  char a[12], c[8];
  uint32_t naddr; uint8_t ncmd; bool rpt;
  uint16_t saddr; uint8_t scmd, sbits;
  bool tog;
  if (decodeNEC(f, naddr, ncmd, rpt)) {
    if (rpt) { naddr = s_last_nec_addr; ncmd = s_last_nec_cmd; }
    else { s_last_nec_addr = naddr; s_last_nec_cmd = ncmd; s_have_nec = true; }
    snprintf(a, sizeof a, naddr > 0xFF ? "0x%04lX" : "0x%02lX", (unsigned long)naddr);
    snprintf(c, sizeof c, "0x%02X", ncmd);
    doc["proto"] = "NEC"; doc["addr"] = a; doc["code"] = c; doc["repeat"] = rpt;
  } else if (decodeSIRC(f, saddr, scmd, sbits)) {
    snprintf(a, sizeof a, "0x%02X", saddr);
    snprintf(c, sizeof c, "0x%02X", scmd);
    doc["proto"] = "SIRC"; doc["addr"] = a; doc["code"] = c; doc["bits"] = sbits; doc["repeat"] = false;
  } else if (decodeRC5(f, saddr, scmd, tog)) {
    snprintf(a, sizeof a, "0x%02X", saddr);
    snprintf(c, sizeof c, "0x%02X", scmd);
    doc["proto"] = "RC5"; doc["addr"] = a; doc["code"] = c; doc["toggle"] = tog; doc["repeat"] = false;
  } else {
    doc["proto"] = "UNKNOWN"; doc["repeat"] = false;
  }
  JsonArray raw = doc["raw"].to<JsonArray>();
  for (uint16_t i = 0; i < f.n; i++) raw.add(f.t[i]);
  doc["t"] = millis();
  proto::send(doc);
}

void poll() {
  if (!s_rx_on || !s_started || s_tx_busy) return;
  noInterrupts();
  bool done = (micros() - s_last) > FRAME_GAP_US;
  if (done) {
    s_frame.n = s_n;
    memcpy(s_frame.t, s_buf, s_n * sizeof(uint16_t));
    s_started = false;
    s_n = 0;
  }
  interrupts();
  if (!done) return;
  // Ignore glitches; a real frame has at least a leader mark+space and a bit.
  if (s_frame.n < 3) return;
  emitFrame(s_frame);
}

// ---------------------------------------------------------------------------
// Transmit
// ---------------------------------------------------------------------------
static void carrier(uint32_t hz) {
  if (hz < 20000 || hz > 60000) hz = 38000;
  uint16_t top = 16000000UL / hz;
  if (top != s_pwm_top) {
    s_pwm_top = top;
    PWM.setMaxValue(top);
  }
}

static void sendTimings(const uint16_t* t, uint16_t n) {
  uint16_t duty = s_pwm_top / 3;
  uint32_t start = micros(), due = 0;
  for (uint16_t i = 0; i < n; i++) {
    PWM.writePin(s_tx_pin, (i & 1) ? 0 : duty);
    due += t[i];
    // Schedule against the frame start so per-step overhead doesn't accumulate.
    while ((int32_t)(micros() - start - due) < 0) {}
  }
  PWM.writePin(s_tx_pin, 0);
}

static void finishTx(bool ok) {
  s_tx_busy = false;
  JsonDocument doc;
  doc["ev"] = "ir_tx_done";
  doc["ok"] = ok;
  doc["t"] = millis();
  proto::send(doc);
}

bool sendRaw(uint32_t hz, const uint16_t* t, uint16_t n) {
  if (!n) { finishTx(false); return false; }
  s_tx_busy = true;
  carrier(hz);
  sendTimings(t, n);
  finishTx(true);
  return true;
}

bool sendNEC(uint32_t addr, uint8_t cmd, uint8_t repeats) {
  static uint16_t t[68];
  uint16_t n = 0;
  t[n++] = 9000; t[n++] = 4500;
  uint32_t v;
  if (addr > 0xFF) v = (addr & 0xFFFF);
  else v = (addr & 0xFF) | (uint32_t)(~addr & 0xFF) << 8;
  v |= (uint32_t)cmd << 16 | (uint32_t)(uint8_t)~cmd << 24;
  for (int b = 0; b < 32; b++) { t[n++] = 560; t[n++] = (v >> b & 1) ? 1690 : 560; }
  t[n++] = 560;
  s_tx_busy = true;
  carrier(38000);
  sendTimings(t, n);
  static const uint16_t rpt[3] = {9000, 2250, 560};
  for (uint8_t r = 0; r < repeats; r++) {
    delay(40);
    sendTimings(rpt, 3);
  }
  finishTx(true);
  return true;
}

bool sendSIRC(uint16_t addr, uint8_t cmd, uint8_t bits) {
  static uint16_t t[2 + 40];
  if (bits != 12 && bits != 15 && bits != 20) bits = 12;
  uint32_t v = (cmd & 0x7F) | (uint32_t)addr << 7;
  s_tx_busy = true;
  carrier(40000);
  for (int rep = 0; rep < 3; rep++) {  // Sony sends each frame 3x
    uint16_t n = 0;
    t[n++] = 2400; t[n++] = 600;
    for (uint8_t b = 0; b < bits; b++) { t[n++] = (v >> b & 1) ? 1200 : 600; t[n++] = 600; }
    sendTimings(t, n - 1);
    delay(25);
  }
  finishTx(true);
  return true;
}

}  // namespace ir
