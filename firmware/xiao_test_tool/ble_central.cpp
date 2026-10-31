#include "ble_central.h"

#include <bluefruit.h>
#include <InternalFileSystem.h>
#include <utility/bonding.h>

#include "hid_map.h"
#include "proto.h"

using namespace Adafruit_LittleFS_Namespace;

namespace blec {

// ---------------------------------------------------------------------------
// Limits / constants
// ---------------------------------------------------------------------------
static const uint8_t MAX_SVC = 20;
static const uint8_t MAX_CHR = 64;
static const uint8_t MAX_DEV = 64;
static const uint16_t GATT_TIMEOUT_MS = 3000;
static const uint32_t CONNECT_TIMEOUT_MS = 8000;

static const uint16_t UUID_HID = 0x1812, UUID_BAS = 0x180F;
static const uint16_t UUID_REPORT = 0x2A4D, UUID_REPORT_MAP = 0x2A4B, UUID_BOOT_KBD_IN = 0x2A22;
static const uint16_t UUID_BATTERY_LEVEL = 0x2A19;
static const uint16_t UUID_CCCD = 0x2902, UUID_REPORT_REF = 0x2908;

// Android TV Voice service AB5E0001-5A21-4F05-BC7D-AF01F617B664 (little-endian bytes);
// bytes 12..13 carry the 16-bit short id: 0001 svc, 0002 TX (write), 0003 audio, 0004 control.
static const uint8_t ATVV_BASE[16] = {0x64, 0xB6, 0x17, 0xF6, 0x01, 0xAF, 0x7D, 0xBC,
                                      0x05, 0x4F, 0x21, 0x5A, 0x00, 0x00, 0x5E, 0xAB};
enum { ATVV_SVC = 1, ATVV_TX = 2, ATVV_AUDIO = 3, ATVV_CTL = 4 };
enum { ATVV_GET_CAPS = 0x0A, ATVV_MIC_OPEN = 0x0C, ATVV_MIC_CLOSE = 0x0D };
enum { ATVV_CTL_STOP = 0x00, ATVV_CTL_START = 0x04, ATVV_CTL_SEARCH = 0x08,
       ATVV_CTL_SYNC = 0x0A, ATVV_CTL_CAPS = 0x0B, ATVV_CTL_MIC_ERR = 0x0C };

// ---------------------------------------------------------------------------
// GATT database model
// ---------------------------------------------------------------------------
struct Svc {
  uint16_t start, end, uuid16;
  bool is128;
  uint8_t uuid128[16];
};

enum ChrRole : uint8_t { R_OTHER, R_HID_REPORT, R_HID_BOOT_KBD, R_HID_MAP, R_BATTERY, R_VOICE_AUDIO, R_VOICE_CTL, R_VOICE_TX, R_VENDOR_NOTIFY };

struct Chr {
  uint8_t svc;
  uint16_t decl, val, end, cccd, rref;
  uint16_t uuid16;
  bool is128;
  uint8_t uuid128[16];
  uint8_t props;          // bit0 broadcast,1 read,2 write_wo_resp,3 write,4 notify,5 indicate
  ChrRole role;
  uint8_t rid, rtype;     // HID report reference
  bool subscribed;
  uint8_t keys_n;         // currently pressed usages on this report
  hid::Usage keys[8];
};

static Svc s_svc[MAX_SVC];
static uint8_t s_nsvc = 0;
static Chr s_chr[MAX_CHR];
static uint8_t s_nchr = 0;
static hid::ReportMap s_map;
static uint8_t s_map_raw[512];
static uint16_t s_map_len = 0;

// ---------------------------------------------------------------------------
// Connection state (written from BLE task, read from loop)
// ---------------------------------------------------------------------------
static volatile uint16_t s_conn = BLE_CONN_HANDLE_INVALID;
static volatile bool s_ev_connected = false, s_ev_disconnected = false, s_ev_secured = false;
static volatile bool s_ev_sec_req = false, s_ev_conn_timeout = false;
static volatile uint8_t s_disc_reason = 0, s_auth_status = 0xFF;
static volatile bool s_ev_auth = false;
static volatile uint8_t s_auth_lesc = 0, s_auth_bonded = 0, s_sec_lv = 0, s_sec_keysize = 0;
static bool s_enabled = true;
static bool s_connecting = false;
static uint32_t s_connect_started = 0;
static bool s_discovered = false, s_ready = false, s_want_bond = false;
static bool s_auto_subscribe = true;
static ble_gap_addr_t s_target;          // last peer we connected to
static bool s_have_target = false;
static bool s_auto_reconnect = false;
static bool s_auto_pair = false;         // pair right after connecting (set when a bond was dropped)
static bool s_reenc_pending = false;     // encryption with stored keys started, not yet confirmed
// Session lock: auto-reconnect only to this remote (identity address), so a stray key press on
// another bonded unit on the bench can't take over the jig. Persisted in /jig_lock.bin.
static bool s_lock_on = false;
static uint8_t s_lock_id[6];
static const char* LOCK_FILE = "/jig_lock.bin";
// One-step "pair": connect, pair, subscribe; retries a connection the remote didn't answer.
static bool s_pair_req = false;
static uint8_t s_pair_tries = 0;
static uint32_t s_retry_at = 0;
static ble_gap_addr_t s_retry_addr;
// Passkey pairing fallback for remotes that refuse Just Works (SMP 0x83).
static bool s_mitm = false;
static volatile bool s_ev_passkey = false;
static char s_passkey[7];
// Link-parameter updates, reported as `link` events.
static volatile bool s_ev_cparams = false, s_ev_phy = false, s_ev_dle = false, s_ev_mtu = false;
static volatile uint16_t s_ci = 0, s_lat = 0, s_sto = 0, s_dle_tx = 0, s_dle_rx = 0, s_mtu_rx = 0;
static volatile uint8_t s_phy_tx = 0, s_phy_rx = 0, s_phy_status = 0;
// RAM copy of the bonds (identity address + IRK): recognising a bonded remote must not read
// flash for every advert of every device nearby.
struct BondId { ble_gap_addr_t id; ble_gap_irk_t irk; };
static const uint8_t MAX_BONDS = 16;
static BondId s_bonds[MAX_BONDS];
static uint8_t s_nbonds = 0;
static uint32_t s_bond_reload_at = 0;      // reload the cache at this time (0 = not pending)
static uint32_t s_last_state_ms = 0;
static const char* s_stage = "idle";

// voice
static bool s_voice_active = false;
static uint32_t s_voice_last_ms = 0;
static uint32_t s_voice_seq = 0;
static uint16_t s_atvv_ver = 0x0004;

// ---------------------------------------------------------------------------
// GATTC request/response plumbing
// ---------------------------------------------------------------------------
static SemaphoreHandle_t s_sem;
static volatile uint16_t s_expect = 0;
static volatile uint16_t s_rsp_status = 0;
static volatile uint16_t s_rsp_count = 0;
static ble_gattc_service_t s_rsp_svc[8];
static ble_gattc_char_t s_rsp_chr[12];
static ble_gattc_desc_t s_rsp_desc[12];
static uint8_t s_rsp_data[248];
static volatile uint16_t s_rsp_len = 0;

struct Hvx { uint16_t handle; uint16_t len; uint8_t data[244]; };
static QueueHandle_t s_hvx_q;
static volatile uint32_t s_hvx_count = 0;   // notifications seen on the current link (diagnostics)
// Link health. This remote only sends input on a link set up while it was in pairing
// mode; a reconnect with stored keys encrypts fine but stays silent. On a working link
// the ATV voice service answers GET_CAPS at once, so a missing reply flags it early.
static uint32_t s_probe_ms = 0, s_probe_hvx = 0;
static int8_t s_live = -1;                     // -1 unknown, 0 silent, 1 sending
// Set once a link proved silent: stop answering the remote's directed reconnects (that
// is what keeps it out of real pairing-mode advertising) and pair fresh on the next
// undirected advert. The stored bond stays until the new pairing replaces it.
static bool s_fresh_pair = false;
static uint32_t s_pairing_ms = 0;              // a pairing we started is still in progress
// Non-zero "now" for timers where 0 means "off" (never round *up*: millis() - t must not wrap).
static uint32_t stamp() { uint32_t t = millis(); return t ? t : 1; }

struct Adv {
  ble_gap_addr_t addr;
  ble_gap_addr_t target;   // directed adverts: who they are addressed to
  int8_t rssi;
  bool directed, scan_rsp, connectable;
  uint8_t len;
  uint8_t data[31];
};
static QueueHandle_t s_adv_q;
static volatile bool s_ev_scan_done = false;

// One scan-table entry per device. Advert and scan response are kept apart: only the advert
// carries the Flags (pairing mode), and a later scan response must not hide them.
struct Dev {
  uint8_t addr[6];
  uint8_t type;
  char name[30];
  uint32_t last_emit, last_seen;
  int8_t rssi;
  bool hid;
  uint16_t appearance;
  uint8_t advlen, rsplen;
  uint8_t adv[31], rsp[31];
};
static Dev s_dev[MAX_DEV];
static uint8_t s_ndev = 0;

static void ble_evt(ble_evt_t* evt) {
  uint16_t id = evt->header.evt_id;
  switch (id) {
    case BLE_GAP_EVT_CONNECTED:
      if (evt->evt.gap_evt.params.connected.role == BLE_GAP_ROLE_CENTRAL) {
        s_conn = evt->evt.gap_evt.conn_handle;
        s_hvx_count = 0;
        const ble_gap_conn_params_t& cp = evt->evt.gap_evt.params.connected.conn_params;
        s_ci = cp.max_conn_interval; s_lat = cp.slave_latency; s_sto = cp.conn_sup_timeout;
        s_ev_cparams = true;
        s_live = -1;
        s_probe_ms = 0;
        s_ev_connected = true;
      }
      return;
    case BLE_GAP_EVT_DISCONNECTED:
      if (evt->evt.gap_evt.conn_handle == s_conn) {
        s_disc_reason = evt->evt.gap_evt.params.disconnected.reason;
        s_conn = BLE_CONN_HANDLE_INVALID;
        s_ev_disconnected = true;
        s_rsp_status = 0xFFFF;
        xSemaphoreGive(s_sem);  // unblock any waiter
      }
      return;
    // ---- link-parameter changes: reported as `link` events from poll() ----
    case BLE_GAP_EVT_CONN_PARAM_UPDATE: {
      const ble_gap_conn_params_t& cp = evt->evt.gap_evt.params.conn_param_update.conn_params;
      s_ci = cp.max_conn_interval; s_lat = cp.slave_latency; s_sto = cp.conn_sup_timeout;
      s_ev_cparams = true;
      return;
    }
    case BLE_GAP_EVT_PHY_UPDATE:
      s_phy_status = evt->evt.gap_evt.params.phy_update.status;
      s_phy_tx = evt->evt.gap_evt.params.phy_update.tx_phy;
      s_phy_rx = evt->evt.gap_evt.params.phy_update.rx_phy;
      s_ev_phy = true;
      return;
    case BLE_GAP_EVT_DATA_LENGTH_UPDATE:
      s_dle_tx = evt->evt.gap_evt.params.data_length_update.effective_params.max_tx_octets;
      s_dle_rx = evt->evt.gap_evt.params.data_length_update.effective_params.max_rx_octets;
      s_ev_dle = true;
      return;
    case BLE_GATTC_EVT_EXCHANGE_MTU_RSP:
      s_mtu_rx = evt->evt.gattc_evt.params.exchange_mtu_rsp.server_rx_mtu;
      s_ev_mtu = true;
      return;
    case BLE_GAP_EVT_PASSKEY_DISPLAY:
      memcpy(s_passkey, evt->evt.gap_evt.params.passkey_display.passkey, 6);
      s_passkey[6] = 0;
      s_ev_passkey = true;
      return;
    case BLE_GAP_EVT_TIMEOUT:
      if (evt->evt.gap_evt.params.timeout.src == BLE_GAP_TIMEOUT_SRC_CONN) s_ev_conn_timeout = true;
      return;
    case BLE_GAP_EVT_SEC_REQUEST:
      s_ev_sec_req = true;
      return;
    case BLE_GAP_EVT_AUTH_STATUS:
      s_auth_status = evt->evt.gap_evt.params.auth_status.auth_status;
      s_auth_lesc = evt->evt.gap_evt.params.auth_status.lesc;
      s_auth_bonded = evt->evt.gap_evt.params.auth_status.bonded;
      s_ev_auth = true;
      return;
    case BLE_GAP_EVT_CONN_SEC_UPDATE:
      s_sec_lv = evt->evt.gap_evt.params.conn_sec_update.conn_sec.sec_mode.lv;
      s_sec_keysize = evt->evt.gap_evt.params.conn_sec_update.conn_sec.encr_key_size;
      if (s_sec_lv >= 2) s_ev_secured = true;
      return;
    case BLE_GATTC_EVT_HVX: {
      const ble_gattc_evt_hvx_t& h = evt->evt.gattc_evt.params.hvx;
      if (h.type == BLE_GATT_HVX_INDICATION)
        sd_ble_gattc_hv_confirm(evt->evt.gattc_evt.conn_handle, h.handle);
      s_hvx_count++;
      Hvx item;
      item.handle = h.handle;
      item.len = h.len > sizeof(item.data) ? sizeof(item.data) : h.len;
      memcpy(item.data, h.data, item.len);
      xQueueSend(s_hvx_q, &item, 0);  // drop if full rather than stall the SD
      return;
    }
    default:
      break;
  }

  if (id < BLE_GATTC_EVT_BASE || id > BLE_GATTC_EVT_LAST || id != s_expect) return;
  const ble_gattc_evt_t& g = evt->evt.gattc_evt;
  s_rsp_status = g.gatt_status;
  switch (id) {
    case BLE_GATTC_EVT_PRIM_SRVC_DISC_RSP: {
      uint16_t n = min<uint16_t>(g.params.prim_srvc_disc_rsp.count, 8);
      memcpy(s_rsp_svc, g.params.prim_srvc_disc_rsp.services, n * sizeof(ble_gattc_service_t));
      s_rsp_count = n;
      break;
    }
    case BLE_GATTC_EVT_CHAR_DISC_RSP: {
      uint16_t n = min<uint16_t>(g.params.char_disc_rsp.count, 12);
      memcpy(s_rsp_chr, g.params.char_disc_rsp.chars, n * sizeof(ble_gattc_char_t));
      s_rsp_count = n;
      break;
    }
    case BLE_GATTC_EVT_DESC_DISC_RSP: {
      uint16_t n = min<uint16_t>(g.params.desc_disc_rsp.count, 12);
      memcpy(s_rsp_desc, g.params.desc_disc_rsp.descs, n * sizeof(ble_gattc_desc_t));
      s_rsp_count = n;
      break;
    }
    case BLE_GATTC_EVT_READ_RSP: {
      uint16_t n = min<uint16_t>(g.params.read_rsp.len, sizeof(s_rsp_data));
      memcpy(s_rsp_data, g.params.read_rsp.data, n);
      s_rsp_len = n;
      break;
    }
    case BLE_GATTC_EVT_WRITE_RSP:
    default:
      break;
  }
  s_expect = 0;
  xSemaphoreGive(s_sem);
}

// The SoftDevice runs one GATT client procedure at a time; right after connect the
// MTU exchange holds it, so retry while it reports BUSY (up to ~1 s).
#define SD_RETRY(call) ({ uint32_t _e; int _n = 0; \
  while ((_e = (call)) == NRF_ERROR_BUSY && _n++ < 100 && s_conn != BLE_CONN_HANDLE_INVALID) delay(10); _e; })

static void arm(uint16_t evt) {
  xSemaphoreTake(s_sem, 0);  // drop any stale give
  s_rsp_count = 0;
  s_rsp_len = 0;
  s_expect = evt;
}

static bool await() {
  bool got = xSemaphoreTake(s_sem, ms2tick(GATT_TIMEOUT_MS)) == pdTRUE;
  s_expect = 0;
  return got && s_conn != BLE_CONN_HANDLE_INVALID;
}

static bool gattcRead(uint16_t handle, uint16_t offset, uint8_t* out, uint16_t* len) {
  arm(BLE_GATTC_EVT_READ_RSP);
  if (SD_RETRY(sd_ble_gattc_read(s_conn, handle, offset)) != NRF_SUCCESS) { s_expect = 0; return false; }
  if (!await() || s_rsp_status != BLE_GATT_STATUS_SUCCESS) return false;
  memcpy(out, s_rsp_data, s_rsp_len);
  *len = s_rsp_len;
  return true;
}

static uint16_t gattcReadLong(uint16_t handle, uint8_t* out, uint16_t max) {
  uint16_t total = 0;
  uint16_t mtu = Bluefruit.Connection(s_conn) ? Bluefruit.Connection(s_conn)->getMtu() : 23;
  while (total < max) {
    uint8_t buf[248];
    uint16_t n = 0;
    if (!gattcRead(handle, total, buf, &n)) break;
    n = min<uint16_t>(n, max - total);
    memcpy(out + total, buf, n);
    total += n;
    if (n < mtu - 1) break;  // short read = end of value
  }
  return total;
}

static bool gattcWrite(uint16_t handle, const uint8_t* data, uint16_t len, bool rsp) {
  ble_gattc_write_params_t p;
  memset(&p, 0, sizeof p);
  p.write_op = rsp ? BLE_GATT_OP_WRITE_REQ : BLE_GATT_OP_WRITE_CMD;
  p.handle = handle;
  p.len = len;
  p.p_value = data;
  if (!rsp) return SD_RETRY(sd_ble_gattc_write(s_conn, &p)) == NRF_SUCCESS;
  arm(BLE_GATTC_EVT_WRITE_RSP);
  if (SD_RETRY(sd_ble_gattc_write(s_conn, &p)) != NRF_SUCCESS) { s_expect = 0; return false; }
  return await() && s_rsp_status == BLE_GATT_STATUS_SUCCESS;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
static void fmtAddr(char* out, const uint8_t* a) {
  sprintf(out, "%02X:%02X:%02X:%02X:%02X:%02X", a[5], a[4], a[3], a[2], a[1], a[0]);
}

static const char* atypeName(uint8_t t) {
  switch (t) {
    case BLE_GAP_ADDR_TYPE_PUBLIC: return "public";
    case BLE_GAP_ADDR_TYPE_RANDOM_STATIC: return "random";
    case BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_RESOLVABLE: return "rpa";
    case BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_NON_RESOLVABLE: return "nrpa";
    default: return "?";
  }
}

static void fmtUuid(char* out, const Chr* c, const Svc* s) {
  bool is128 = c ? c->is128 : s->is128;
  const uint8_t* u = c ? c->uuid128 : s->uuid128;
  uint16_t u16 = c ? c->uuid16 : s->uuid16;
  if (!is128) { sprintf(out, "%04X", u16); return; }
  sprintf(out, "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X",
          u[15], u[14], u[13], u[12], u[11], u[10], u[9], u[8], u[7], u[6], u[5], u[4], u[3], u[2], u[1], u[0]);
}

static int atvvId(const uint8_t* u) {
  for (int i = 0; i < 16; i++)
    if (i != 12 && i != 13 && u[i] != ATVV_BASE[i]) return 0;
  return u[12] | u[13] << 8;
}

static Chr* chrByHandle(uint16_t h) {
  for (uint8_t i = 0; i < s_nchr; i++)
    if (s_chr[i].val == h) return &s_chr[i];
  return nullptr;
}

static Chr* chrByRole(ChrRole r) {
  for (uint8_t i = 0; i < s_nchr; i++)
    if (s_chr[i].role == r) return &s_chr[i];
  return nullptr;
}

static const char* propStr(uint8_t p, char* out) {
  char* o = out;
  if (p & 0x02) *o++ = 'R';
  if (p & 0x04) *o++ = 'w';
  if (p & 0x08) *o++ = 'W';
  if (p & 0x10) *o++ = 'N';
  if (p & 0x20) *o++ = 'I';
  *o = 0;
  return out;
}

// ---------------------------------------------------------------------------
// State reporting
// ---------------------------------------------------------------------------
static void sendGatt();
static void sendHid();

// Re-send what a freshly attached host (e.g. a restarted bridge) has missed: the GATT
// table and the HID report map of the current link.
void sendInfo() {
  if (connected() && s_nchr) { sendGatt(); sendHid(); readRemoteBattery(); }
  sendBonds();
  sendState();
}

void sendState() {
  JsonDocument doc;
  doc["ev"] = "state";
  BLEConnection* c = s_conn != BLE_CONN_HANDLE_INVALID ? Bluefruit.Connection(s_conn) : nullptr;
  doc["conn"] = c != nullptr;
  doc["stage"] = s_stage;
  doc["auto_reconnect"] = s_auto_reconnect;
  if (s_have_target) {
    char a[18];
    fmtAddr(a, s_target.addr);
    doc["addr"] = a;
  }
  if (c) {
    doc["bonded"] = c->bonded();
    doc["secured"] = c->secured();
    doc["mtu"] = min<uint16_t>(c->getMtu(), BLE_GATT_ATT_MTU_MAX);
    uint8_t phy = c->getPHY();
    doc["phy"] = phy == BLE_GAP_PHY_2MBPS ? "2M" : phy == BLE_GAP_PHY_CODED ? "coded" : "1M";
    doc["rssi"] = c->getRssi();
    doc["interval_ms"] = c->getConnectionInterval() * 1.25f;
  }
  doc["services"] = s_nsvc;
  doc["ntf"] = s_hvx_count;
  if (s_lock_on) {
    char la[18];
    fmtAddr(la, s_lock_id);
    doc["lock"] = la;
  }
  doc["bonds"] = s_nbonds;
  if (c && s_live >= 0) doc["live"] = s_live == 1;
  proto::send(doc);
  s_last_state_ms = millis();
}

static void setStage(const char* st) {
  s_stage = st;
  sendState();
}

static void sendGatt() {
  JsonDocument doc;
  doc["ev"] = "gatt";
  JsonArray svcs = doc["services"].to<JsonArray>();
  char buf[40], h[8], pr[8];
  for (uint8_t si = 0; si < s_nsvc; si++) {
    JsonObject so = svcs.add<JsonObject>();
    fmtUuid(buf, nullptr, &s_svc[si]);
    so["uuid"] = buf;
    sprintf(h, "0x%04X", s_svc[si].start);
    so["start"] = h;
    sprintf(h, "0x%04X", s_svc[si].end);
    so["end"] = h;
    JsonArray chars = so["chars"].to<JsonArray>();
    for (uint8_t ci = 0; ci < s_nchr; ci++) {
      Chr& c = s_chr[ci];
      if (c.svc != si) continue;
      JsonObject co = chars.add<JsonObject>();
      fmtUuid(buf, &c, nullptr);
      co["uuid"] = buf;
      sprintf(h, "0x%04X", c.val);
      co["handle"] = h;
      co["props"] = propStr(c.props, pr);
      if (c.role == R_HID_REPORT && c.rref) {
        co["report_id"] = c.rid;
        co["report_type"] = c.rtype == 1 ? "input" : c.rtype == 2 ? "output" : c.rtype == 3 ? "feature" : "?";
      }
      static const char* ROLE[] = {"", "hid_report", "hid_boot_kbd", "hid_map", "battery", "voice_audio", "voice_ctl", "voice_tx", "vendor_notify"};
      if (c.role != R_OTHER) co["role"] = ROLE[c.role];
    }
  }
  proto::send(doc);
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------
static bool discoverServices() {
  s_nsvc = 0;
  uint16_t start = 1;
  while (s_nsvc < MAX_SVC) {
    arm(BLE_GATTC_EVT_PRIM_SRVC_DISC_RSP);
    uint32_t err = SD_RETRY(sd_ble_gattc_primary_services_discover(s_conn, start, nullptr));
    if (err != NRF_SUCCESS) { s_expect = 0; proto::log("error", "svc discover err 0x%lX", (unsigned long)err); return false; }
    if (!await()) return false;
    if (s_rsp_status == BLE_GATT_STATUS_ATTERR_ATTRIBUTE_NOT_FOUND || s_rsp_count == 0) break;
    if (s_rsp_status != BLE_GATT_STATUS_SUCCESS) return false;
    uint16_t last_end = 0;
    for (uint16_t i = 0; i < s_rsp_count && s_nsvc < MAX_SVC; i++) {
      Svc& s = s_svc[s_nsvc++];
      s.start = s_rsp_svc[i].handle_range.start_handle;
      s.end = s_rsp_svc[i].handle_range.end_handle;
      s.uuid16 = s_rsp_svc[i].uuid.uuid;
      s.is128 = s_rsp_svc[i].uuid.type != BLE_UUID_TYPE_BLE;
      memset(s.uuid128, 0, 16);
      last_end = s.end;
    }
    if (last_end == 0xFFFF) break;
    start = last_end + 1;
  }
  // 128-bit service UUIDs: read the service declaration value.
  for (uint8_t i = 0; i < s_nsvc; i++) {
    if (!s_svc[i].is128) continue;
    uint16_t n = 0;
    if (gattcRead(s_svc[i].start, 0, s_rsp_data, &n)) {
      if (n >= 16) memcpy(s_svc[i].uuid128, s_rsp_data, 16);
      else if (n == 2) { s_svc[i].is128 = false; s_svc[i].uuid16 = s_rsp_data[0] | s_rsp_data[1] << 8; }
    }
  }
  return true;
}

static void discoverChars(uint8_t si) {
  Svc& s = s_svc[si];
  uint8_t first = s_nchr;
  ble_gattc_handle_range_t r = {s.start, s.end};
  while (r.start_handle <= r.end_handle && s_nchr < MAX_CHR) {
    arm(BLE_GATTC_EVT_CHAR_DISC_RSP);
    if (SD_RETRY(sd_ble_gattc_characteristics_discover(s_conn, &r)) != NRF_SUCCESS) { s_expect = 0; break; }
    if (!await() || s_rsp_status != BLE_GATT_STATUS_SUCCESS || s_rsp_count == 0) break;
    for (uint16_t i = 0; i < s_rsp_count && s_nchr < MAX_CHR; i++) {
      const ble_gattc_char_t& gc = s_rsp_chr[i];
      Chr& c = s_chr[s_nchr++];
      memset(&c, 0, sizeof c);
      c.svc = si;
      c.decl = gc.handle_decl;
      c.val = gc.handle_value;
      c.uuid16 = gc.uuid.uuid;
      c.is128 = gc.uuid.type != BLE_UUID_TYPE_BLE;
      c.props = (gc.char_props.broadcast) | (gc.char_props.read << 1) | (gc.char_props.write_wo_resp << 2) |
                (gc.char_props.write << 3) | (gc.char_props.notify << 4) | (gc.char_props.indicate << 5);
    }
    r.start_handle = s_chr[s_nchr - 1].val + 1;
  }
  // End handle of each characteristic, 128-bit UUIDs from the declaration value.
  for (uint8_t i = first; i < s_nchr; i++) {
    s_chr[i].end = (i + 1 < s_nchr && s_chr[i + 1].svc == si) ? s_chr[i + 1].decl - 1 : s.end;
    if (s_chr[i].is128) {
      // Declaration value: props(1) handle(2) uuid(2 or 16). Some remotes pad the
      // response (seen: 20 bytes), and some declare 16-bit UUIDs the SD reports as unknown.
      uint16_t n = 0;
      if (gattcRead(s_chr[i].decl, 0, s_rsp_data, &n)) {
        if (n >= 19) memcpy(s_chr[i].uuid128, s_rsp_data + 3, 16);
        else if (n >= 5) { s_chr[i].is128 = false; s_chr[i].uuid16 = s_rsp_data[3] | s_rsp_data[4] << 8; }
      }
    }
  }
}

static void discoverDescs(Chr& c) {
  ble_gattc_handle_range_t r = {(uint16_t)(c.val + 1), c.end};
  while (r.start_handle <= r.end_handle) {
    arm(BLE_GATTC_EVT_DESC_DISC_RSP);
    if (SD_RETRY(sd_ble_gattc_descriptors_discover(s_conn, &r)) != NRF_SUCCESS) { s_expect = 0; return; }
    if (!await() || s_rsp_status != BLE_GATT_STATUS_SUCCESS || s_rsp_count == 0) return;
    for (uint16_t i = 0; i < s_rsp_count; i++) {
      if (s_rsp_desc[i].uuid.type != BLE_UUID_TYPE_BLE) continue;
      if (s_rsp_desc[i].uuid.uuid == UUID_CCCD) c.cccd = s_rsp_desc[i].handle;
      else if (s_rsp_desc[i].uuid.uuid == UUID_REPORT_REF) c.rref = s_rsp_desc[i].handle;
    }
    r.start_handle = s_rsp_desc[s_rsp_count - 1].handle + 1;
  }
}

static void assignRoles() {
  for (uint8_t i = 0; i < s_nchr; i++) {
    Chr& c = s_chr[i];
    const Svc& s = s_svc[c.svc];
    c.role = R_OTHER;
    if (!s.is128 && s.uuid16 == UUID_HID && !c.is128) {
      if (c.uuid16 == UUID_REPORT) c.role = R_HID_REPORT;
      else if (c.uuid16 == UUID_BOOT_KBD_IN) c.role = R_HID_BOOT_KBD;
      else if (c.uuid16 == UUID_REPORT_MAP) c.role = R_HID_MAP;
    } else if (!s.is128 && s.uuid16 == UUID_BAS && c.uuid16 == UUID_BATTERY_LEVEL) {
      c.role = R_BATTERY;
    } else if (s.is128 && c.is128) {
      int id = atvvId(c.uuid128);
      if (id == ATVV_AUDIO) c.role = R_VOICE_AUDIO;
      else if (id == ATVV_CTL) c.role = R_VOICE_CTL;
      else if (id == ATVV_TX) c.role = R_VOICE_TX;
      else if (c.props & 0x30) c.role = R_VENDOR_NOTIFY;
    }
  }
  // Vendor voice services other than ATVV: if a 128-bit service has no ATVV
  // roles, treat its largest-handle notify char as audio and the first as control.
  if (!chrByRole(R_VOICE_AUDIO)) {
    for (uint8_t i = 0; i < s_nchr; i++)
      if (s_chr[i].role == R_VENDOR_NOTIFY) s_chr[i].role = R_VOICE_AUDIO;
  }
}

static void runDiscovery() {
  setStage("discovering");
  uint32_t t0 = millis();
  s_nchr = 0;
  if (!discoverServices()) {
    proto::log("error", "service discovery failed");
    setStage("connected");
    return;
  }
  for (uint8_t i = 0; i < s_nsvc && s_conn != BLE_CONN_HANDLE_INVALID; i++) discoverChars(i);
  for (uint8_t i = 0; i < s_nchr && s_conn != BLE_CONN_HANDLE_INVALID; i++)
    if ((s_chr[i].props & 0x30) || (s_chr[i].uuid16 == UUID_REPORT && !s_chr[i].is128)) discoverDescs(s_chr[i]);
  assignRoles();
  s_discovered = s_conn != BLE_CONN_HANDLE_INVALID;
  proto::log("info", "discovered %u services, %u chars in %lu ms", s_nsvc, s_nchr, millis() - t0);
  sendGatt();
  setStage("discovered");
}

// Reads that usually need an encrypted link: report map, report refs, battery.
static void sendHid() {
  if (!s_map_len) return;
  JsonDocument doc;
  doc["ev"] = "hid";
  doc["map_len"] = s_map_len;
  doc["fields"] = s_map.n_fields;
  doc["uses_ids"] = s_map.uses_ids;
  static char hx[sizeof(s_map_raw) * 2 + 1];
  proto::hex(hx, s_map_raw, s_map_len);
  doc["map"] = hx;
  JsonArray reps = doc["reports"].to<JsonArray>();
  char h[8];
  for (uint8_t i = 0; i < s_nchr; i++) {
    Chr& c = s_chr[i];
    if (c.role != R_HID_REPORT && c.role != R_HID_BOOT_KBD) continue;
    JsonObject r = reps.add<JsonObject>();
    sprintf(h, "0x%04X", c.val);
    r["handle"] = h;
    r["id"] = c.rid;
    r["type"] = c.role == R_HID_BOOT_KBD ? "boot_kbd" : c.rtype == 1 ? "input" : c.rtype == 2 ? "output" : c.rtype == 3 ? "feature" : "?";
  }
  proto::send(doc);
}

static void runPostSecurity() {
  setStage("reading");
  Chr* m = chrByRole(R_HID_MAP);
  s_map.clear();
  s_map_len = 0;
  if (m) {
    s_map_len = gattcReadLong(m->val, s_map_raw, sizeof s_map_raw);
    if (s_map_len) s_map.parse(s_map_raw, s_map_len);
    else proto::log("warn", "report map read failed (status 0x%04X) — bond first?", s_rsp_status);
  }
  for (uint8_t i = 0; i < s_nchr; i++) {
    Chr& c = s_chr[i];
    if (c.role != R_HID_REPORT || !c.rref) continue;
    uint8_t buf[4];
    uint16_t n = 0;
    if (gattcRead(c.rref, 0, buf, &n) && n >= 2) { c.rid = buf[0]; c.rtype = buf[1]; }
  }
  sendHid();
  readRemoteBattery();
  s_ready = true;
  if (s_auto_subscribe) subscribe("all");
  sendGatt();  // now with report ids
  setStage("ready");
}

// ---------------------------------------------------------------------------
// Subscriptions
// ---------------------------------------------------------------------------
static bool wantsRole(ChrRole r, const char* what) {
  bool all = !strcmp(what, "all");
  if (r == R_HID_REPORT || r == R_HID_BOOT_KBD) return all || !strcmp(what, "hid");
  if (r == R_VOICE_AUDIO || r == R_VOICE_CTL) return all || !strcmp(what, "voice");
  if (r == R_BATTERY) return all || !strcmp(what, "battery");
  return false;
}

static void atvvSend(const uint8_t* d, uint16_t n) {
  Chr* tx = chrByRole(R_VOICE_TX);
  if (!tx) { proto::log("warn", "no voice TX characteristic"); return; }
  gattWrite(tx->val, d, n, tx->props & 0x08);
}

void subscribe(const char* what) {
  if (!connected() || !s_discovered) { proto::log("warn", "subscribe: not connected/discovered"); return; }
  uint8_t ok = 0, fail = 0;
  for (uint8_t i = 0; i < s_nchr; i++) {
    Chr& c = s_chr[i];
    if (!c.cccd || !(c.props & 0x30) || !wantsRole(c.role, what)) continue;
    if (c.role == R_HID_REPORT && c.rref && c.rtype != 1) continue;  // only input reports
    uint8_t v[2] = {(uint8_t)((c.props & 0x10) ? 1 : 2), 0};
    if (gattcWrite(c.cccd, v, 2, true)) { c.subscribed = true; ok++; }
    else { fail++; proto::log("warn", "CCCD 0x%04X write failed (status 0x%04X)", c.cccd, s_rsp_status); }
  }
  proto::log("info", "subscribed %s: %u ok, %u failed", what, ok, fail);
  if ((!strcmp(what, "all") || !strcmp(what, "voice")) && chrByRole(R_VOICE_TX)) {
    // Introduce ourselves as an ATV Voice v1.0 host (codecs: ADPCM 8 + 16 kHz; interaction
    // models: on-request + hold-to-talk). A v1.0 remote that is only told "v0.4" keeps its mic
    // button inactive; with v1.0 caps it streams by itself while the button is held.
    const uint8_t caps[] = {ATVV_GET_CAPS, 0x01, 0x00, 0x00, 0x03, 0x03};
    // Close any session left half-open (a stream cut off by the remote's ~15 s cap
    // without AUDIO_STOP leaves its voice service unresponsive until it is closed).
    const uint8_t close[] = {ATVV_MIC_CLOSE};
    atvvSend(close, 1);
    delay(30);
    atvvSend(caps, sizeof caps);
    s_probe_hvx = s_hvx_count;
    s_probe_ms = stamp();
  }
}

void mic(bool open) {
  if (!connected()) return;
  if (open) {
    // Hold-to-talk remotes answer MIC_OPEN_ERROR 0x0F01; they only stream while the button is held.
    const uint8_t open[] = {ATVV_MIC_OPEN, 0x00, 0x01};
    atvvSend(open, sizeof open);
  } else {
    const uint8_t c[] = {ATVV_MIC_CLOSE};
    atvvSend(c, 1);
  }
}

void readRemoteBattery() {
  Chr* b = chrByRole(R_BATTERY);
  if (!b || !connected()) return;
  uint8_t v = 0;
  uint16_t n = 0;
  if (gattcRead(b->val, 0, &v, &n) && n >= 1) {
    JsonDocument doc;
    doc["ev"] = "battery";
    doc["which"] = "remote";
    doc["pct"] = v;
    doc["via"] = "read";      // Battery Level 0x2A19 read (on connect / on request)
    proto::send(doc);
  }
}

bool gattWrite(uint16_t handle, const uint8_t* data, uint16_t len, bool with_rsp) {
  if (!connected()) return false;
  bool ok = gattcWrite(handle, data, len, with_rsp);
  if (!ok) proto::log("warn", "write 0x%04X failed (status 0x%04X)", handle, s_rsp_status);
  return ok;
}

bool gattRead(uint16_t handle) {
  if (!connected()) return false;
  if (s_voice_active) {
    // A read blocks the loop for up to a connection event x slave latency; during voice that
    // overflows the notification queue. The app retries after the stream.
    JsonDocument doc;
    doc["ev"] = "gatt_read";
    char h[8];
    sprintf(h, "0x%04X", handle);
    doc["handle"] = h;
    doc["ok"] = false;
    doc["busy"] = true;
    proto::send(doc);
    return false;
  }
  static uint8_t buf[512];
  uint16_t n = gattcReadLong(handle, buf, sizeof buf);
  JsonDocument doc;
  doc["ev"] = "gatt_read";
  char h[8];
  sprintf(h, "0x%04X", handle);
  doc["handle"] = h;
  doc["ok"] = n > 0 || s_rsp_status == BLE_GATT_STATUS_SUCCESS;
  static char hx[sizeof(buf) * 2 + 1];
  proto::hex(hx, buf, n);
  doc["bytes"] = hx;
  proto::send(doc);
  return n > 0;
}

// ---------------------------------------------------------------------------
// Notification handling
// ---------------------------------------------------------------------------
static bool sameUsage(const hid::Usage& a, const hid::Usage& b) { return a.page == b.page && a.id == b.id; }

static void emitKey(const Chr& c, const hid::Usage& u, bool down, const char* report) {
  JsonDocument doc;
  doc["ev"] = "key";
  doc["t"] = millis();
  char h[8], id[8];
  sprintf(h, "0x%04X", c.val);
  doc["handle"] = h;
  doc["report"] = report;
  const char* nm = hid::name(u.page, u.id);
  sprintf(id, "0x%X", u.id);
  doc["usage"] = nm ? nm : id;
  doc["usage_id"] = id;
  doc["page"] = hid::pageName(u.page);
  doc["rid"] = c.rid;
  doc["down"] = down;
  proto::send(doc);
}

static void handleHidReport(Chr& c, const uint8_t* d, uint16_t n) {
  hid::Usage now[8];
  uint8_t nn = 0;
  if (c.role == R_HID_BOOT_KBD) {
    for (uint8_t b = 0; b < 8 && nn < 8; b++)
      if (n > 0 && (d[0] >> b & 1)) now[nn++] = {0x07, (uint16_t)(0xE0 + b)};
    for (uint16_t i = 2; i < n && i < 8 && nn < 8; i++)
      if (d[i] > 1) now[nn++] = {0x07, d[i]};
  } else if (s_map.valid) {
    uint8_t rid = s_map.uses_ids ? c.rid : 0;
    if (!s_map.hasKeys(rid)) {
      // A report with no key fields (vendor data: motion, pointer, voice …): pass it on raw.
      JsonDocument doc;
      doc["ev"] = "hid_data";
      char hs[8];
      sprintf(hs, "0x%04X", c.val);
      doc["handle"] = hs;
      doc["rid"] = c.rid;
      static char hx[2 * 244 + 1];
      proto::hex(hx, d, n);
      doc["bytes"] = hx;
      doc["t"] = millis();
      proto::send(doc);
      return;
    }
    nn = s_map.decode(rid, d, n, now, 8);
  }
  if (nn == 0 && !s_map.valid) {
    // No report map: assume a 16-bit consumer-style array, report each non-zero word.
    for (uint16_t i = 0; i + 1 < n + 1 && nn < 8; i += 2) {
      uint16_t v = d[i] | (i + 1 < n ? d[i + 1] << 8 : 0);
      if (v) now[nn++] = {0, v};
    }
  }
  char rep[3 * 32 + 1];
  proto::hex(rep, d, min<uint16_t>(n, 32), true);
  // released
  for (uint8_t i = 0; i < c.keys_n; i++) {
    bool still = false;
    for (uint8_t j = 0; j < nn; j++) still |= sameUsage(c.keys[i], now[j]);
    if (!still) emitKey(c, c.keys[i], false, rep);
  }
  // pressed
  for (uint8_t j = 0; j < nn; j++) {
    bool was = false;
    for (uint8_t i = 0; i < c.keys_n; i++) was |= sameUsage(c.keys[i], now[j]);
    if (!was) emitKey(c, now[j], true, rep);
  }
  memcpy(c.keys, now, nn * sizeof(hid::Usage));
  c.keys_n = nn;
}

static void voiceState(bool active) {
  if (active == s_voice_active) return;
  s_voice_active = active;
  JsonDocument doc;
  doc["ev"] = "voice_state";
  doc["active"] = active;
  doc["t"] = millis();
  proto::send(doc);
}

static void handleVoiceCtl(const uint8_t* d, uint16_t n) {
  JsonDocument doc;
  doc["ev"] = "voice_ctl";
  const char* op = "unknown";
  if (n) {
    switch (d[0]) {
      case ATVV_CTL_STOP: op = "stop"; break;
      case ATVV_CTL_START: op = "start"; break;
      case ATVV_CTL_SEARCH: op = "search"; break;
      case ATVV_CTL_SYNC: op = "sync"; break;
      case ATVV_CTL_CAPS: op = "caps"; break;
      case ATVV_CTL_MIC_ERR: op = "mic_error"; break;
    }
  }
  doc["op"] = op;
  char hx[64];
  proto::hex(hx, d, min<uint16_t>(n, 31));
  doc["bytes"] = hx;
  doc["t"] = millis();
  proto::send(doc);
  if (!n) return;
  if (d[0] == ATVV_CTL_CAPS && n >= 3) s_atvv_ver = d[1] << 8 | d[2];
  if (d[0] == ATVV_CTL_SEARCH) mic(true);  // mic button: open the mic like a TV would
  if (d[0] == ATVV_CTL_START) { s_voice_seq = 0; voiceState(true); }
  if (d[0] == ATVV_CTL_STOP) voiceState(false);
}

static void handleHvx(const Hvx& h) {
  Chr* c = chrByHandle(h.handle);
  char hx[sizeof(h.data) * 2 + 1];
  if (c && (c->role == R_HID_REPORT || c->role == R_HID_BOOT_KBD)) {
    handleHidReport(*c, h.data, h.len);
    return;
  }
  if (c && c->role == R_VOICE_CTL) { handleVoiceCtl(h.data, h.len); return; }
  JsonDocument doc;
  char hs[8];
  sprintf(hs, "0x%04X", h.handle);
  if (c && c->role == R_VOICE_AUDIO) {
    voiceState(true);
    s_voice_last_ms = millis();
    doc["ev"] = "voice";
    doc["seq"] = s_voice_seq++;
    doc["codec"] = "adpcm";
    doc["handle"] = hs;
    proto::hex(hx, h.data, h.len);
    doc["bytes"] = hx;
    doc["t"] = millis();
  } else if (c && c->role == R_BATTERY && h.len) {
    doc["ev"] = "battery";
    doc["which"] = "remote";
    doc["pct"] = h.data[0];
    doc["via"] = "notify";    // pushed by the remote after we enabled its CCCD
  } else {
    doc["ev"] = "notify";
    doc["handle"] = hs;
    proto::hex(hx, h.data, h.len);
    doc["bytes"] = hx;
    doc["t"] = millis();
  }
  proto::send(doc);
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------
static void scan_cb(ble_gap_evt_adv_report_t* r) {
  Adv a;
  a.addr = r->peer_addr;
  a.rssi = r->rssi;
  a.directed = r->type.directed;
  a.target = r->direct_addr;
  a.scan_rsp = r->type.scan_response;
  a.connectable = r->type.connectable;
  a.len = min<uint16_t>(r->data.len, sizeof a.data);
  memcpy(a.data, r->data.p_data, a.len);
  xQueueSend(s_adv_q, &a, 0);
  Bluefruit.Scanner.resume();
}

static void scan_stop_cb() { s_ev_scan_done = true; }

// ---------------------------------------------------------------------------
// Bonds: RAM cache, identity lookup, removal by identity, session lock
// ---------------------------------------------------------------------------
static void loadBondCache() {
  s_nbonds = 0;
  File dir = InternalFS.open(BOND_DIR_CNTR);
  if (!dir) return;
  File f = dir.openNextFile();
  while (f && s_nbonds < MAX_BONDS) {
    bond_keys_t k;
    if (f.read() == sizeof(bond_keys_t) && f.read((uint8_t*)&k, sizeof k) == sizeof k) {
      s_bonds[s_nbonds].id = k.peer_id.id_addr_info;
      s_bonds[s_nbonds].irk = k.peer_id.id_info;
      s_nbonds++;
    }
    f.close();
    f = dir.openNextFile();
  }
  if (f) f.close();
  dir.close();
}

static bool irkSet(const ble_gap_irk_t& k) {
  for (uint8_t i = 0; i < sizeof k.irk; i++) if (k.irk[i]) return true;
  return false;
}

// Identity address of a bonded remote seen at `a` (public/static address, or an RPA resolved
// with its IRK). False if `a` belongs to no bonded remote.
static bool findBond(const ble_gap_addr_t& a, ble_gap_addr_t* id) {
  for (uint8_t i = 0; i < s_nbonds; i++) {
    bool hit = !memcmp(s_bonds[i].id.addr, a.addr, 6);
    if (!hit && a.addr_type == BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_RESOLVABLE && irkSet(s_bonds[i].irk)) {
      ble_gap_addr_t t = a;
      hit = Bluefruit.Security.resolveAddress(&t, &s_bonds[i].irk);
    }
    if (hit) { if (id) *id = s_bonds[i].id; return true; }
  }
  return false;
}

// Bluefruit files a bond under the remote's *identity* address, which differs from the
// connection address for remotes using private addresses — remove by identity.
static void removeBond(const ble_gap_addr_t& a) {
  ble_gap_addr_t id;
  bond_remove_key(BLE_GAP_ROLE_CENTRAL, findBond(a, &id) ? &id : &a);
  loadBondCache();
}

static void saveLock() {
  if (s_lock_on) {
    File f = InternalFS.open(LOCK_FILE, FILE_O_WRITE);
    if (f) { f.seek(0); f.write(s_lock_id, 6); f.close(); }
  } else {
    InternalFS.remove(LOCK_FILE);
  }
}

static void setLock(const uint8_t* addr6) {
  if (addr6) { memcpy(s_lock_id, addr6, 6); s_lock_on = true; }
  else s_lock_on = false;
  saveLock();
}

static bool lockMatches(const ble_gap_addr_t& a) {
  if (!s_lock_on) return true;
  if (!memcmp(a.addr, s_lock_id, 6)) return true;
  ble_gap_addr_t id;
  return findBond(a, &id) && !memcmp(id.addr, s_lock_id, 6);
}

// A bonded remote the jig should follow: bonded, and the session's DUT if a lock is set.
static bool isTarget(const ble_gap_addr_t& a) {
  if (!findBond(a, nullptr) && !(s_have_target && !memcmp(a.addr, s_target.addr, 6))) return false;
  if (lockMatches(a)) return true;
  static uint32_t last = 0;
  if (!last || millis() - last > 30000) {
    last = millis();
    char t[18];
    fmtAddr(t, a.addr);
    proto::log("info", "ignoring bonded remote %s: the session is locked to another remote (unlock or pair it)", t);
  }
  return false;
}

// Flags AD (type 0x01) of an advert, or -1 if absent.
static int advFlags(const uint8_t* d, uint8_t len) {
  for (uint8_t i = 0; i + 1 < len && d[i];) {
    uint8_t l = d[i];
    if (d[i + 1] == 0x01 && l >= 2 && i + 2 < len) return d[i + 2];
    i += l + 1;
  }
  return -1;
}

// Scan with no timeout so a bonded remote that wakes up (key press -> directed
// advert to the jig) is reconnected straight away.
static void startBackgroundScan() {
  Bluefruit.Scanner.stop();
  Bluefruit.Scanner.setInterval(160, 160);  // full duty: directed adverts last only 1.28 s
  Bluefruit.Scanner.start(0);
}

static void startConnect(const ble_gap_addr_t& a) {
  Bluefruit.Scanner.stop();
  // Reconnecting to the same bonded peer: keep its GATT database. A bonded HID
  // remote resumes notifying as soon as the link is encrypted (it remembers our
  // CCCDs), so rediscovering would drop the key press that woke it up.
  bool same = s_have_target && !memcmp(s_target.addr, a.addr, 6) && s_nchr > 0;
  if (!same) { s_nsvc = s_nchr = 0; s_map.clear(); }
  s_discovered = same;
  s_ready = false;
  s_target = a;
  s_have_target = true;
  s_connecting = true;
  s_connect_started = millis();
  // Not Bluefruit.Central.connect(): it reuses the scanner params, and our scan
  // filter policy (report unresolved directed adverts) is invalid for connecting.
  ble_gap_scan_params_t sp = *Bluefruit.Scanner.getParams();
  sp.filter_policy = BLE_GAP_SCAN_FP_ACCEPT_ALL;
  sp.active = 0;
  sp.timeout = CONNECT_TIMEOUT_MS / 10;
  ble_gap_conn_params_t cp = {6, 12, 0, 400};  // 7.5–15 ms interval, 4 s supervision timeout
  uint32_t err = sd_ble_gap_connect(&a, &sp, &cp, CONN_CFG_CENTRAL);
  if (err != NRF_SUCCESS) {
    s_connecting = false;
    proto::log("error", "connect request rejected (err 0x%lX)", (unsigned long)err);
    setStage("idle");
    return;
  }
  setStage("connecting");
}

// A bonded remote addresses its directed adverts to a resolvable private address
// it derives from the IRK we gave it when bonding — not to our public address.
static bool directedAtUs(const Adv& a) {
  if (!a.directed) return false;
  ble_gap_addr_t own = Bluefruit.getAddr();
  if (!memcmp(own.addr, a.target.addr, 6)) return true;
  if ((a.target.addr[5] >> 6) != 0b01) return false;   // not an RPA
  static ble_gap_irk_t irk;
  static bool have_irk = false;
  if (!have_irk) {
    ble_gap_privacy_params_t pp;
    memset(&pp, 0, sizeof pp);
    pp.p_device_irk = &irk;
    have_irk = sd_ble_gap_privacy_get(&pp) == NRF_SUCCESS;
  }
  ble_gap_addr_t t = a.target;
  t.addr_type = BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_RESOLVABLE;
  return have_irk && Bluefruit.Security.resolveAddress(&t, &irk);
}

static void processAdv(const Adv& a) {
  // Follow a bonded remote back. Only the advert (not a scan response) tells what it wants:
  //   undirected + LE-discoverable flags  -> pairing mode: pair fresh (old keys are void)
  //   directed at the jig, or undirected + connectable but not discoverable -> reconnect
  //   with the stored keys (unless fresh-pair mode: that link would be silent again)
  //   anything else (directed at another host, not connectable) -> not for the jig
  if (!a.scan_rsp && s_auto_reconnect && !connected() && !s_connecting && isTarget(a.addr)) {
    int fl = advFlags(a.data, a.len);
    bool pairing = !a.directed && a.connectable && fl >= 0 && (fl & 0x03);
    bool reconnect = a.directed ? directedAtUs(a) : (a.connectable && !pairing);
    if (pairing) {
      s_fresh_pair = true;
      proto::log("info", "bonded remote is in pairing mode (discoverable advert, flags 0x%02X): pairing fresh", fl);
      // Drop the old bond *before* connecting: Bluefruit re-encrypts with any stored bond the moment
      // a link opens (BLECentral, BLE task), which would beat our fresh pairing.
      ble_gap_addr_t id;
      if (findBond(a.addr, &id)) {
        removeBond(a.addr);
        if (s_lock_on && !memcmp(s_lock_id, id.addr, 6)) memcpy(s_lock_id, a.addr.addr, 6);  // re-promoted after pairing
        proto::log("info", "removed the old bond first: the remote discarded it when entering pairing mode");
      }
      startConnect(a.addr);
      return;
    }
    if (reconnect && s_fresh_pair) {
      static uint32_t last_skip = 0;
      if (!last_skip || millis() - last_skip > 10000) {
        last_skip = millis();
        proto::log("info", "ignoring the remote's reconnect (that link would be silent); "
                   "waiting for its pairing-mode advert to pair fresh");
      }
      return;
    }
    if (reconnect) {
      proto::log("info", "bonded remote advertising (%s), reconnecting with the stored keys",
                 a.directed ? "directed at the jig" : "undirected, not discoverable");
      startConnect(a.addr);
      return;
    }
    static uint32_t last_note = 0;
    if (!last_note || millis() - last_note > 30000) {
      last_note = millis();
      char t[18];
      fmtAddr(t, a.target.addr);
      proto::log("warn", "bonded remote is advertising %s%s, which is not for the jig — it is "
                 "reconnecting to another host it is bonded with", a.directed ? "directed to " : "non-connectable",
                 a.directed ? t : "");
    }
  }

  // ---- scan table: one entry per device, oldest evicted when full ----
  Dev* d = nullptr;
  for (uint8_t i = 0; i < s_ndev; i++)
    if (!memcmp(s_dev[i].addr, a.addr.addr, 6)) { d = &s_dev[i]; break; }
  bool fresh = false;
  if (!d) {
    if (s_ndev < MAX_DEV) d = &s_dev[s_ndev++];
    else {
      d = &s_dev[0];
      for (uint8_t i = 1; i < s_ndev; i++) if (s_dev[i].last_seen < d->last_seen) d = &s_dev[i];
    }
    memset(d, 0, sizeof *d);
    memcpy(d->addr, a.addr.addr, 6);
    fresh = true;
  }
  d->type = a.addr.addr_type;
  d->rssi = a.rssi;
  d->last_seen = millis();
  bool changed = false;
  if (a.scan_rsp) {
    changed = d->rsplen != a.len || memcmp(d->rsp, a.data, a.len);
    d->rsplen = a.len; memcpy(d->rsp, a.data, a.len);
  } else {
    changed = d->advlen != a.len || memcmp(d->adv, a.data, a.len);
    d->advlen = a.len; memcpy(d->adv, a.data, a.len);
  }
  uint8_t nm[30];
  uint8_t nl = Bluefruit.Scanner.parseReportByType(a.data, a.len, BLE_GAP_AD_TYPE_COMPLETE_LOCAL_NAME, nm, sizeof nm - 1);
  if (!nl) nl = Bluefruit.Scanner.parseReportByType(a.data, a.len, BLE_GAP_AD_TYPE_SHORT_LOCAL_NAME, nm, sizeof nm - 1);
  if (nl && !d->name[0]) { memcpy(d->name, nm, nl); d->name[nl] = 0; fresh = true; }
  uint8_t u[16];
  uint8_t ul = Bluefruit.Scanner.parseReportByType(a.data, a.len, BLE_GAP_AD_TYPE_16BIT_SERVICE_UUID_COMPLETE, u, sizeof u);
  if (!ul) ul = Bluefruit.Scanner.parseReportByType(a.data, a.len, BLE_GAP_AD_TYPE_16BIT_SERVICE_UUID_MORE_AVAILABLE, u, sizeof u);
  for (uint8_t i = 0; i + 1 < ul; i += 2) d->hid |= (u[i] | u[i + 1] << 8) == UUID_HID;
  uint8_t app[2];
  if (Bluefruit.Scanner.parseReportByType(a.data, a.len, BLE_GAP_AD_TYPE_APPEARANCE, app, 2) == 2) {
    d->appearance = app[0] | app[1] << 8;
    d->hid |= (d->appearance >> 6) == 0x0F;  // HID category
  }
  if (!fresh && !changed && millis() - d->last_emit < 1000) return;
  d->last_emit = millis();

  JsonDocument doc;
  doc["ev"] = "scan";
  char s[18];
  fmtAddr(s, a.addr.addr);
  doc["addr"] = s;
  doc["atype"] = atypeName(a.addr.addr_type);
  doc["rssi"] = a.rssi;
  doc["name"] = d->name;
  doc["directed"] = a.directed;
  if (a.directed) {
    char t[18];
    fmtAddr(t, a.target.addr);
    doc["target"] = t;
    doc["to_jig"] = directedAtUs(a);
  }
  doc["connectable"] = a.connectable;
  if (d->appearance) doc["appearance"] = d->appearance;
  doc["hid"] = d->hid;
  doc["bonded"] = findBond(a.addr, nullptr);
  char hx[63];
  proto::hex(hx, d->adv, d->advlen);
  doc["adv"] = hx;                 // advertising data (carries the Flags)
  proto::hex(hx, d->rsp, d->rsplen);
  doc["rsp"] = hx;                 // scan response data
  proto::send(doc);
}

bool scan(uint16_t sec) {
  if (!s_enabled) return false;
  if (connected()) { proto::log("warn", "scan while connected is disabled; disconnect first"); return false; }
  Bluefruit.Scanner.stop();
  s_ndev = 0;
  Bluefruit.Scanner.setInterval(160, 80);
  Bluefruit.Scanner.start(sec * 100);  // 10 ms units; 0 = forever
  proto::log("info", "scanning %us", sec);
  return true;
}

void scanStop() { Bluefruit.Scanner.stop(); }

// ---------------------------------------------------------------------------
// Connection control
// ---------------------------------------------------------------------------
static bool parseAddr(const char* s, ble_gap_addr_t& out, const char* atype) {
  unsigned b[6];
  if (!s || sscanf(s, "%x:%x:%x:%x:%x:%x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6) return false;
  memset(&out, 0, sizeof out);
  for (int i = 0; i < 6; i++) out.addr[5 - i] = b[i];
  // Prefer the exact type the scanner saw.
  for (uint8_t i = 0; i < s_ndev; i++)
    if (!memcmp(s_dev[i].addr, out.addr, 6)) { out.addr_type = s_dev[i].type; return true; }
  if (atype && !strcmp(atype, "public")) out.addr_type = BLE_GAP_ADDR_TYPE_PUBLIC;
  else {
    uint8_t top = out.addr[5] >> 6;
    out.addr_type = top == 3 ? BLE_GAP_ADDR_TYPE_RANDOM_STATIC
                  : top == 1 ? BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_RESOLVABLE
                             : BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_NON_RESOLVABLE;
  }
  return true;
}

bool connect(const char* addr, const char* atype) {
  if (!s_enabled) return false;
  if (connected() || s_connecting) { proto::log("warn", "already connected/connecting"); return false; }
  ble_gap_addr_t a;
  if (!parseAddr(addr, a, atype)) { proto::log("error", "bad address"); return false; }
  s_auto_reconnect = false;
  // The remote the operator picked becomes the session's DUT (identity if already bonded).
  ble_gap_addr_t id;
  setLock(findBond(a, &id) ? id.addr : a.addr);
  startConnect(a);
  return true;
}

// One step: connect, pair (Just Works; passkey fallback), subscribe. Retries a connection the
// remote didn't answer (0x3E / timeout) twice — remotes often miss the first CONNECT_IND.
bool pair(const char* addr, const char* atype) {
  if (connected()) {
    if (addr && *addr) {
      ble_gap_addr_t a;
      if (parseAddr(addr, a, atype) && memcmp(a.addr, s_target.addr, 6)) {
        proto::log("warn", "pair: connected to another remote — disconnect first");
        return false;
      }
    }
    return bond();
  }
  ble_gap_addr_t a0;
  if (parseAddr(addr, a0, atype) && findBond(a0, nullptr)) {
    // An explicit Pair means pair anew. Remove the old bond before connecting: Bluefruit would
    // otherwise re-encrypt with it as soon as the link opens.
    removeBond(a0);
    proto::log("info", "removed the old bond with %s: pairing fresh", addr);
  }
  if (!connect(addr, atype)) return false;
  s_pair_req = true;
  s_fresh_pair = true;
  s_pair_tries = 2;
  parseAddr(addr, s_retry_addr, atype);
  proto::log("info", "pairing %s: connect → pair → subscribe", addr);
  return true;
}

void lock(const char* addr) {
  if (!addr || !*addr) { setLock(nullptr); proto::log("info", "session unlocked: the jig follows any bonded remote"); }
  else {
    ble_gap_addr_t a, id;
    if (!parseAddr(addr, a, nullptr)) { proto::log("error", "lock: bad address"); return; }
    setLock(findBond(a, &id) ? id.addr : a.addr);
    proto::log("info", "session locked to %s: auto-reconnect ignores other remotes", addr);
  }
  sendState();
}

void sendBonds() {
  loadBondCache();
  JsonDocument doc;
  doc["ev"] = "bonds";
  JsonArray list = doc["list"].to<JsonArray>();
  for (uint8_t i = 0; i < s_nbonds; i++) {
    JsonObject o = list.add<JsonObject>();
    char t[18];
    fmtAddr(t, s_bonds[i].id.addr);
    o["addr"] = t;
    o["atype"] = atypeName(s_bonds[i].id.addr_type);
    o["rpa"] = irkSet(s_bonds[i].irk);           // remote uses private addresses (IRK stored)
    o["locked"] = s_lock_on && !memcmp(s_lock_id, s_bonds[i].id.addr, 6);
    ble_gap_addr_t cur;
    o["connected"] = connected() && findBond(s_target, &cur) && !memcmp(cur.addr, s_bonds[i].id.addr, 6);
  }
  proto::send(doc);
}

void bondDelete(const char* addr) {
  ble_gap_addr_t a;
  if (!parseAddr(addr, a, nullptr)) { proto::log("error", "bond_delete: bad address"); return; }
  bool had = findBond(a, nullptr);
  removeBond(a);
  if (s_lock_on && !memcmp(s_lock_id, a.addr, 6)) setLock(nullptr);
  proto::log(had ? "info" : "warn", had ? "bond with %s deleted" : "no bond with %s", addr);
  if (s_nbonds == 0) s_auto_reconnect = false;
  sendBonds();
}

static bool encryptWithStoredKeys() {
  BLEConnection* c = Bluefruit.Connection(s_conn);
  if (!c) return false;
  bond_keys_t k;
  if (!c->loadBondKey(&k)) return false;
  proto::log("info", "re-encrypting with stored bond");
  s_reenc_pending = Bluefruit.Security._encrypt(s_conn, &k);
  return s_reenc_pending;
}

bool bond() {
  BLEConnection* c = connected() ? Bluefruit.Connection(s_conn) : nullptr;
  if (!c) { proto::log("warn", "bond: not connected"); return false; }
  if (c->secured()) { proto::log("info", "already secured"); if (!s_ready) runPostSecurity(); return true; }
  s_want_bond = true;
  setStage("pairing");
  if (encryptWithStoredKeys()) return true;
  s_pairing_ms = stamp();
  return c->requestPairing();
}

// Re-run pairing on an existing (possibly already encrypted) link. Diagnostic: some
// remotes only send notifications on a link where pairing actually took place.
bool repair() {
  if (!connected()) return false;
  ble_gap_sec_params_t sp;
  memset(&sp, 0, sizeof sp);
  sp.bond = 1;
  sp.lesc = 1;
  sp.io_caps = BLE_GAP_IO_CAPS_NONE;
  sp.min_key_size = 7;
  sp.max_key_size = 16;
  sp.kdist_own.enc = 1;
  sp.kdist_own.id = 1;
  sp.kdist_peer.enc = 1;
  sp.kdist_peer.id = 1;
  uint32_t err = SD_RETRY(sd_ble_gap_authenticate(s_conn, &sp));
  proto::log(err ? "error" : "info", "re-pairing requested (err 0x%lX)", (unsigned long)err);
  if (!err) { setStage("pairing"); s_pairing_ms = stamp(); }
  return err == NRF_SUCCESS;
}

void disconnect() {
  s_auto_reconnect = false;
  if (s_connecting) { sd_ble_gap_connect_cancel(); s_connecting = false; setStage("idle"); }
  if (connected()) sd_ble_gap_disconnect(s_conn, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
}

void clearBonds() {
  Bluefruit.Central.clearBonds();
  loadBondCache();
  setLock(nullptr);
  s_auto_reconnect = false;
  proto::log("info", "bonds cleared");
  sendBonds();
}

void setEnabled(bool on) {
  s_enabled = on;
  if (!on) { Bluefruit.Scanner.stop(); disconnect(); }
}

bool connected() { return s_conn != BLE_CONN_HANDLE_INVALID; }

uint8_t bondCount() { return s_nbonds; }

// ---------------------------------------------------------------------------
// Persistent identity key. The SoftDevice makes a random IRK at every boot; a
// bonded remote addresses its directed "wake-up" adverts to an RPA derived from
// the IRK it received when bonding, so the IRK must survive reboots or the jig
// can never answer them.
static const char* IRK_FILE = "/jig_irk.bin";

static void restoreIrk() {
  ble_gap_irk_t irk;
  ble_gap_privacy_params_t pp;
  memset(&pp, 0, sizeof pp);
  pp.p_device_irk = &irk;
  File f = InternalFS.open(IRK_FILE, FILE_O_READ);
  if (f && f.read(irk.irk, sizeof irk.irk) == sizeof irk.irk) {
    f.close();
    pp.privacy_mode = BLE_GAP_PRIVACY_MODE_OFF;
    pp.private_addr_type = BLE_GAP_ADDR_TYPE_RANDOM_PRIVATE_RESOLVABLE;
    pp.private_addr_cycle_s = BLE_GAP_DEFAULT_PRIVATE_ADDR_CYCLE_INTERVAL_S;
    uint32_t err = sd_ble_gap_privacy_set(&pp);
    if (err != NRF_SUCCESS) proto::log("error", "restoring IRK failed: 0x%lX", (unsigned long)err);
    return;
  }
  if (f) f.close();
  if (sd_ble_gap_privacy_get(&pp) != NRF_SUCCESS) return;
  f = InternalFS.open(IRK_FILE, FILE_O_WRITE);
  if (f) { f.write(irk.irk, sizeof irk.irk); f.close(); }
}

void begin() {
  s_sem = xSemaphoreCreateBinary();
  s_hvx_q = xQueueCreate(64, sizeof(Hvx));    // ~160 ms of voice audio
  s_adv_q = xQueueCreate(48, sizeof(Adv));

  Bluefruit.configCentralBandwidth(BANDWIDTH_MAX);  // MTU 247, long event length
  Bluefruit.begin(0, 1);
  Bluefruit.setName("XIAO-TestTool");
  Bluefruit.setTxPower(4);
  Bluefruit.autoConnLed(false);
  Bluefruit.setEventCallback(ble_evt);
  restoreIrk();
  Bluefruit.Security.setIOCaps(false, false, false);  // Just Works
  Bluefruit.Security.setMITM(false);

  Bluefruit.Scanner.setRxCallback(scan_cb);
  Bluefruit.Scanner.setStopCallback(scan_stop_cb);
  Bluefruit.Scanner.restartOnDisconnect(false);
  Bluefruit.Scanner.useActiveScan(true);
  Bluefruit.Scanner.setInterval(160, 80);  // 100 ms / 50 ms
  // Also report directed adverts whose target RPA we can't resolve.
  Bluefruit.Scanner.getParams()->filter_policy = BLE_GAP_SCAN_FP_ALL_NOT_RESOLVED_DIRECTED;
  Bluefruit.Central.setConnInterval(6, 12);  // 7.5–15 ms: low key latency
  loadBondCache();
  File lf = InternalFS.open(LOCK_FILE, FILE_O_READ);
  if (lf) { s_lock_on = lf.read(s_lock_id, 6) == 6; lf.close(); }
  if (bondCount() > 0) {
    s_auto_reconnect = true;
    startBackgroundScan();
  }
}

void poll() {
  Adv a;
  for (int i = 0; i < 8 && xQueueReceive(s_adv_q, &a, 0) == pdTRUE; i++) processAdv(a);

  if (s_ev_scan_done) {
    s_ev_scan_done = false;
    if (s_auto_reconnect && s_enabled && !connected() && !s_connecting) startBackgroundScan();
    JsonDocument doc;
    doc["ev"] = "scan_done";
    doc["found"] = s_ndev;
    proto::send(doc);
  }

  if (s_ev_conn_timeout || (s_connecting && !s_ev_connected && millis() - s_connect_started > CONNECT_TIMEOUT_MS)) {
    s_ev_conn_timeout = false;
    if (s_connecting) {
      sd_ble_gap_connect_cancel();
      s_connecting = false;
      proto::log("error", "connect timed out (the remote stopped advertising or didn't answer)");
      setStage("idle");
      if (s_pair_req && s_pair_tries) {
        s_pair_tries--;
        proto::log("info", "retrying the connection (%u left)", s_pair_tries);
        s_retry_at = stamp();
      } else {
        if (s_pair_req) proto::log("error", "pair: gave up — is the remote in pairing mode (PAIRING badge)?");
        s_pair_req = false;
        if (s_auto_reconnect && s_enabled) startBackgroundScan();
      }
    }
  }

  if (s_ev_connected) {
    s_ev_connected = false;
    s_connecting = false;
    BLEConnection* c = Bluefruit.Connection(s_conn);
    proto::log("info", "connected");
    if (c) {
      c->monitorRssi();
      c->requestPHY(BLE_GAP_PHY_2MBPS);
      c->requestDataLengthUpdate();
      c->requestMtuExchange(247);
    }
    setStage("connected");
    if (s_fresh_pair && s_have_target && findBond(s_target, nullptr)) {
      // Bluefruit answers the remote's security request by re-encrypting with any stored
      // bond, which beats our pairing request. The remote is in pairing mode, so that bond is
      // void anyway: drop it (by identity address) so this connection really pairs fresh.
      removeBond(s_target);
      proto::log("info", "removed the old bond: the remote is in pairing mode and pairs fresh");
    }
    // Resume a stored bond before discovery so encrypted reads work right away.
    bool resumed = !s_fresh_pair && encryptWithStoredKeys();
    if (!resumed && (s_auto_pair || s_fresh_pair || s_pair_req) && c) {
      if (s_fresh_pair) proto::log("info", "pairing fresh");
      c->requestPairing();
      s_pairing_ms = stamp();
      setStage("pairing");
    }
    if (!s_discovered) runDiscovery();
    else setStage("discovered");   // cached database; post-security runs on CONN_SEC_UPDATE
    if (resumed && c && c->secured() && !s_ready) runPostSecurity();
  }

  if (s_ev_sec_req) {
    s_ev_sec_req = false;
    BLEConnection* c = connected() ? Bluefruit.Connection(s_conn) : nullptr;
    if (c && !c->secured()) {
      proto::log("info", "peripheral requested security");
      if (s_fresh_pair || !encryptWithStoredKeys()) { c->requestPairing(); s_pairing_ms = stamp(); }
      setStage("pairing");
    }
  }

  if (s_ev_auth) {
    s_ev_auth = false;
    bool was_pairing = s_pairing_ms != 0;
    s_pairing_ms = 0;
    bool mitm_was = s_mitm;
    if (s_mitm) {                      // back to Just Works for everything else
      s_mitm = false;
      Bluefruit.Security.setIOCaps(false, false, false);
      Bluefruit.Security.setMITM(false);
    }
    if (s_auth_status == BLE_GAP_SEC_STATUS_SUCCESS) {
      proto::log("info", "pairing complete (bonded=%u, LESC=%u%s)", s_auth_bonded, s_auth_lesc,
                 mitm_was ? ", authenticated with passkey" : "");
      s_fresh_pair = false;
      s_pair_req = false;
      s_bond_reload_at = stamp();      // bond is written in the background: refresh the cache shortly
      if (s_ready) setStage("ready");   // re-pair on a link that was already set up
      // This remote answers only once the keys are exchanged: re-run the voice handshake,
      // which also re-arms the silent-link check.
      if (was_pairing && s_ready && s_live != 1) subscribe("all");
    }
    else if (s_auth_status == BLE_GAP_SEC_STATUS_AUTH_REQ && !mitm_was && connected()) {
      // The remote insists on authenticated pairing. Retry once as a display device: the
      // remote's user types the 6-digit passkey shown in the app.
      proto::log("warn", "pairing failed: 0x%02X — retrying with passkey entry", s_auth_status);
      s_mitm = true;
      Bluefruit.Security.setIOCaps(true, false, false);
      Bluefruit.Security.setMITM(true);
      BLEConnection* c = Bluefruit.Connection(s_conn);
      if (c && c->requestPairing()) { s_pairing_ms = stamp(); setStage("pairing"); }
    }
    else {
      proto::log("error", "pairing failed: 0x%02X", s_auth_status);
      s_pair_req = false;
      setStage(s_discovered ? "discovered" : "connected");
    }
  }

  if (s_ev_secured) {
    s_ev_secured = false;
    s_auto_reconnect = true;  // remotes drop the link when idle; follow them back
    s_auto_pair = false;
    s_reenc_pending = false;
    proto::log("info", "link encrypted (security level %u, key %u bytes)", s_sec_lv, s_sec_keysize);
    sendState();
    if (s_discovered && !s_ready) runPostSecurity();
  }

  if (s_ev_disconnected) {
    s_ev_disconnected = false;
    s_connecting = false;
    s_ready = false;           // keep s_discovered/the database for a bonded reconnect
    voiceState(false);
    for (uint8_t i = 0; i < s_nchr; i++) s_chr[i].keys_n = 0;
    proto::log("info", "disconnected (reason 0x%02X)", s_disc_reason);
    // A remote put back into pairing mode discards its keys for us: it answers our
    // re-encryption with "key missing" / MIC failure, or (Firestick/ATV) simply drops
    // the link before encryption completes. Either way the stored bond is dead.
    // Only reasons that mean "the remote has no keys for us" — a dropped radio link (0x08) or a
    // failed connection must not cost a good bond.
    bool reenc_rejected = s_reenc_pending &&
        (s_disc_reason == BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION || s_disc_reason == BLE_HCI_AUTHENTICATION_FAILURE ||
         s_disc_reason == 0x0E /* connection rejected: security reasons */);
    s_reenc_pending = false;
    s_pairing_ms = 0;
    if ((s_disc_reason == BLE_HCI_STATUS_CODE_PIN_OR_KEY_MISSING || s_disc_reason == BLE_HCI_CONN_TERMINATED_DUE_TO_MIC_FAILURE
         || reenc_rejected) && s_have_target && findBond(s_target, nullptr)) {
      // Forget our side too (by identity address), so the next connection pairs from scratch.
      removeBond(s_target);
      s_nchr = 0;
      proto::log("warn", "remote no longer has our bond; removed it — next connection will pair fresh");
      s_auto_pair = true;
    }
    setStage("idle");
    if (s_pair_req && s_disc_reason == BLE_HCI_CONN_FAILED_TO_BE_ESTABLISHED && s_pair_tries) {
      s_pair_tries--;
      proto::log("info", "the remote didn't answer the connection; retrying (%u left)", s_pair_tries);
      s_retry_at = stamp();
    } else {
      if (s_pair_req) proto::log("error", "pair: the link dropped before pairing finished");
      s_pair_req = false;
      if (s_auto_reconnect && s_enabled) {
        startBackgroundScan();
        proto::log("info", "waiting for bonded remote to reconnect");
      }
    }
  }

  Hvx h;
  for (int i = 0; i < 16 && xQueueReceive(s_hvx_q, &h, 0) == pdTRUE; i++) handleHvx(h);

  if (s_retry_at && millis() - s_retry_at > 400 && !connected() && !s_connecting) {
    s_retry_at = 0;
    startConnect(s_retry_addr);
  }

  if (s_bond_reload_at && millis() - s_bond_reload_at > 1500) {
    s_bond_reload_at = 0;
    loadBondCache();
    ble_gap_addr_t id;
    // A lock set from the connection address becomes the remote's identity once bonded.
    if (s_lock_on && connected() && findBond(s_target, &id) && memcmp(id.addr, s_lock_id, 6)) setLock(id.addr);
    sendBonds();
    sendState();
  }

  if (s_ev_passkey) {
    s_ev_passkey = false;
    JsonDocument doc;
    doc["ev"] = "passkey";
    doc["code"] = s_passkey;
    proto::send(doc);
    proto::log("warn", "passkey pairing: type %s on the remote, then press OK / Enter", s_passkey);
  }
  if (s_ev_cparams) {
    s_ev_cparams = false;
    JsonDocument doc;
    doc["ev"] = "link";
    doc["what"] = "conn_params";
    doc["interval_ms"] = s_ci * 1.25f;
    doc["latency"] = s_lat;
    doc["timeout_ms"] = s_sto * 10;
    proto::send(doc);
  }
  if (s_ev_phy) {
    s_ev_phy = false;
    static const char* P[] = {"auto", "1M", "2M", "?", "coded"};
    JsonDocument doc;
    doc["ev"] = "link";
    doc["what"] = "phy";
    doc["tx"] = P[s_phy_tx < 5 ? s_phy_tx : 3];
    doc["rx"] = P[s_phy_rx < 5 ? s_phy_rx : 3];
    doc["status"] = s_phy_status;
    proto::send(doc);
  }
  if (s_ev_dle) {
    s_ev_dle = false;
    JsonDocument doc;
    doc["ev"] = "link";
    doc["what"] = "data_length";
    doc["tx_octets"] = s_dle_tx;
    doc["rx_octets"] = s_dle_rx;
    proto::send(doc);
  }
  if (s_ev_mtu) {
    s_ev_mtu = false;
    JsonDocument doc;
    doc["ev"] = "link";
    doc["what"] = "mtu";
    doc["remote_mtu"] = s_mtu_rx;
    uint16_t rx = s_mtu_rx;
    doc["mtu"] = min<uint16_t>(min<uint16_t>(rx, (uint16_t)247), (uint16_t)BLE_GATT_ATT_MTU_MAX);
    proto::send(doc);
  }

  if (connected() && s_live != 1 && s_hvx_count) {
    s_live = 1;
    s_probe_ms = 0;
    sendState();
  }
  // Never judge (or drop) a link while our pairing is still exchanging keys.
  if (s_pairing_ms && s_probe_ms) s_probe_ms = stamp();
  if (s_pairing_ms && millis() - s_pairing_ms > 15000) {
    s_pairing_ms = 0;
    proto::log("warn", "pairing did not complete within 15 s");
  }
  if (s_probe_ms && millis() - s_probe_ms > 3000) {
    s_probe_ms = 0;
    if (connected() && s_hvx_count == s_probe_hvx) {
      s_live = 0;
      s_fresh_pair = true;
      proto::log("warn", "remote is connected but not sending (no reply to the voice handshake): it only sends "
                 "on a link made in pairing mode — dropping this link; put the remote in pairing mode (STB off) "
                 "and the jig will pair it fresh");
      sendState();
      // A silent link is useless and keeps the remote busy; free it (auto-reconnect stays on,
      // but directed reconnects are now ignored, see s_fresh_pair).
      sd_ble_gap_disconnect(s_conn, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
    }
  }

  if (s_voice_active && millis() - s_voice_last_ms > 1500 && s_voice_last_ms) {
    // Audio stopped without AUDIO_STOP: end the session ourselves.
    s_voice_last_ms = 0;
    voiceState(false);
    if (connected()) {
      const uint8_t close[] = {ATVV_MIC_CLOSE};
      atvvSend(close, 1);
    }
  }

  if (connected() && millis() - s_last_state_ms > 2000) sendState();
}

}  // namespace blec
