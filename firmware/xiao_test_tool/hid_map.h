// hid_map.h — minimal HID report-descriptor parser + input-report decoder.
// Mirror of parse_report_map()/decode_report() in backend/hid_usage.py.
#pragma once

#include <Arduino.h>

namespace hid {

struct Usage { uint8_t page; uint16_t id; };

struct Field {
  uint8_t  report_id;
  uint16_t bit_offset;
  uint8_t  size;
  uint8_t  count;
  uint8_t  page;
  bool     variable;
  uint8_t  n_usages;
  uint16_t usages[8];
  int32_t  usage_min;    // -1 if absent
  int32_t  usage_max;
  int32_t  logical_min;
};

class ReportMap {
 public:
  void clear() { n_fields = 0; valid = false; uses_ids = false; }
  bool parse(const uint8_t* desc, size_t len);
  // Fills out[] with active usages; returns count.
  uint8_t decode(uint8_t report_id, const uint8_t* data, size_t len, Usage* out, uint8_t max) const;
  // True if the report has at least one field that can carry key usages (a usage list or range).
  bool hasKeys(uint8_t report_id) const;
  bool valid = false;
  bool uses_ids = false;
  uint8_t n_fields = 0;
  Field fields[24];
};

const char* name(uint8_t page, uint16_t id);       // nullptr if unknown
const char* pageName(uint8_t page);

}  // namespace hid
