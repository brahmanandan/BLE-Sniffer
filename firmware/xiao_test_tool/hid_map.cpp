#include "hid_map.h"

#include "usage_names.h"

namespace hid {

bool ReportMap::parse(const uint8_t* d, size_t len) {
  clear();
  struct Glob { uint8_t page; int32_t lmin; uint8_t size; uint8_t count; uint8_t rid; } g = {0, 0, 0, 0, 0};
  Glob stack[4];
  uint8_t sp = 0;
  uint16_t usages[8];
  uint8_t nu = 0;
  int32_t umin = -1, umax = -1;
  uint16_t offsets[16] = {0};   // bit offset per report id (ids folded into 16 slots)

  size_t i = 0;
  while (i < len) {
    uint8_t p = d[i];
    if (p == 0xFE) { i += 3 + (i + 1 < len ? d[i + 1] : 0); continue; }
    static const uint8_t SZ[4] = {0, 1, 2, 4};
    uint8_t sz = SZ[p & 3], typ = (p >> 2) & 3, tag = p >> 4;
    if (i + 1 + sz > len) break;
    uint32_t val = 0;
    for (uint8_t k = 0; k < sz; k++) val |= (uint32_t)d[i + 1 + k] << (8 * k);
    int32_t sval = val;
    if (sz == 1) sval = (int8_t)val;
    else if (sz == 2) sval = (int16_t)val;
    i += 1 + sz;

    if (typ == 1) {  // global
      switch (tag) {
        case 0x0: g.page = val; break;
        case 0x1: g.lmin = sval; break;
        case 0x7: g.size = val; break;
        case 0x8: g.rid = val; uses_ids = true; break;
        case 0x9: g.count = val; break;
        case 0xA: if (sp < 4) stack[sp++] = g; break;
        case 0xB: if (sp) g = stack[--sp]; break;
      }
    } else if (typ == 2) {  // local
      if (sz == 4) val &= 0xFFFF;
      if (tag == 0x0 && nu < 8) usages[nu++] = val;
      else if (tag == 0x1) umin = val;
      else if (tag == 0x2) umax = val;
    } else if (typ == 0) {  // main
      if (tag == 0x8) {  // Input
        uint16_t& off = offsets[g.rid & 15];
        if (!(val & 0x01) && n_fields < sizeof(fields) / sizeof(fields[0])) {
          Field& f = fields[n_fields++];
          f.report_id = g.rid;
          f.bit_offset = off;
          f.size = g.size;
          f.count = g.count;
          f.page = g.page;
          f.variable = val & 0x02;
          f.n_usages = nu;
          memcpy(f.usages, usages, sizeof(usages));
          f.usage_min = umin;
          f.usage_max = umax;
          f.logical_min = g.lmin;
        }
        off += (uint16_t)g.size * g.count;
      }
      nu = 0;
      umin = umax = -1;
    }
  }
  valid = n_fields > 0;
  return valid;
}

static uint32_t bits(const uint8_t* data, size_t len, uint16_t off, uint8_t n) {
  uint32_t v = 0;
  for (uint8_t k = 0; k < n && k < 32; k++) {
    uint16_t b = off + k;
    if ((b >> 3) < len && (data[b >> 3] >> (b & 7) & 1)) v |= 1UL << k;
  }
  return v;
}

uint8_t ReportMap::decode(uint8_t rid, const uint8_t* data, size_t len, Usage* out, uint8_t max) const {
  uint8_t n = 0;
  for (uint8_t fi = 0; fi < n_fields && n < max; fi++) {
    const Field& f = fields[fi];
    if (f.report_id != rid) continue;
    for (uint8_t k = 0; k < f.count && n < max; k++) {
      uint32_t v = bits(data, len, f.bit_offset + (uint16_t)k * f.size, f.size);
      if (!v) continue;
      int32_t u;
      if (f.variable) {
        if (f.size != 1) continue;  // axes etc. are not keys
        if (f.n_usages) u = f.usages[k < f.n_usages ? k : f.n_usages - 1];
        else if (f.usage_min >= 0) u = f.usage_min + k;
        else continue;
      } else {
        // Array: the value is an index into the field's usage list / range. A field that declares
        // neither (e.g. LG Magic Remote: Consumer page, 0..255, no usages) carries opaque vendor
        // data, not key codes — never treat the raw byte as a usage.
        int32_t idx = (int32_t)v - f.logical_min;
        if (f.usage_min >= 0) u = f.usage_min + idx;
        else if (f.n_usages && idx >= 0 && idx < f.n_usages) u = f.usages[idx];
        else continue;
      }
      if (u > 0) out[n++] = {f.page, (uint16_t)u};
    }
  }
  return n;
}

bool ReportMap::hasKeys(uint8_t rid) const {
  for (uint8_t fi = 0; fi < n_fields; fi++) {
    const Field& f = fields[fi];
    if (f.report_id != rid) continue;
    if (f.variable ? (f.size == 1 && (f.n_usages || f.usage_min >= 0)) : (f.n_usages || f.usage_min >= 0)) return true;
  }
  return false;
}

const char* name(uint8_t page, uint16_t id) {
  for (unsigned i = 0; i < USAGE_NAMES_COUNT; i++)
    if (USAGE_NAMES[i].page == page && USAGE_NAMES[i].usage == id) return USAGE_NAMES[i].name;
  return nullptr;
}

const char* pageName(uint8_t page) {
  switch (page) {
    case 0x01: return "desktop";
    case 0x07: return "keyboard";
    case 0x0C: return "consumer";
    default:   return "unknown";
  }
}

}  // namespace hid
