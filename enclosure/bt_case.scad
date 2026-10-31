// bt_case.scad — parametric alternative to the provided enclosure (bt_case_base/lid.stl,
// also in ../enclosure/) for XIAO nRF52840 + 41x10x6 mm LiPo (brief §11, §12).
// Outer ≈ 46 x 23 x 14 mm, flush friction lid. Edit the parameters to fit other parts.
//
// Render:  openscad -D 'part="base"' -o bt_case_param_base.stl bt_case.scad
//          openscad -D 'part="lid"'  -o bt_case_param_lid.stl  bt_case.scad
//          openscad -D 'part="pads"' -o bt_case_param_pads.stl bt_case.scad   (TPU, or 1 mm silicone bumpers)
//
// Cell retention (§12): the pouch lies in a floor channel. Four corner columns stop
// one pad-thickness below a shelf plane `shelf_gap` (0.3 mm) above the pouch top; soft
// pads on them carry the PCB, so the board retains the cell without pressing on it.
// A hard spacer wall keeps the pouch out of the USB-C slot end.
part = "base";           // "base" | "lid" | "pads" | "both"

$fn = 32;
wall = 1.4;
floor_t = 1.2;
outer = [46, 23, 14];
lid_t = 1.4;             // lid plate, sits in a rebate flush with the rim
rebate = 0.8;            // rebate width cut into the wall top for the flush lid
lip_h = 2.0;             // friction lip depth under the lid plate
fit = 0.15;              // clearance per side for the friction fit
r = 2.0;                 // corner radius

board = [21.5, 17.8, 1.2];      // XIAO PCB
batt = [41, 10, 6];             // LiPo pouch
usb = [9.4, 3.8];               // USB-C opening (w x h)
shelf_gap = 0.3;                // board underside above the pouch top
pad = [3.0, 3.0, 1.0];          // soft stand-off pads (TPU / silicone)
spacer_t = 1.0;                 // hard wall between pouch and USB-C end

inner = [outer[0] - 2 * wall, outer[1] - 2 * wall];
cy = outer[1] / 2;
pouch_top = floor_t + batt[2];
shelf_z = pouch_top + shelf_gap;          // board underside
board_x0 = wall + 0.2;                    // board sits against the USB-C end
spacer_x = wall + 0.6;
pouch_x0 = spacer_x + spacer_t + 0.2;     // pouch starts behind the spacer
corner_xy = [for (x = [board_x0 + 0.2, board_x0 + board[0] - pad[0] - 0.2])
             for (y = [cy - board[1] / 2 + 0.2, cy + board[1] / 2 - pad[1] - 0.2]) [x, y]];

assert(pouch_x0 + batt[0] <= outer[0] - wall, "pouch does not fit the inner length");
assert(cy - board[1] / 2 + pad[1] < cy - batt[1] / 2 - 0.3, "corner columns would touch the pouch");

module rbox(s, rr) {
  hull() for (x = [rr, s[0] - rr], y = [rr, s[1] - rr]) translate([x, y, 0]) cylinder(r = rr, h = s[2]);
}

module base() {
  difference() {
    rbox(outer, r);
    // cavity
    translate([wall, wall, floor_t]) rbox([inner[0], inner[1], outer[2]], r - wall / 2);
    // rebate for the flush lid plate
    translate([wall - rebate, wall - rebate, outer[2] - lid_t])
      rbox([inner[0] + 2 * rebate, inner[1] + 2 * rebate, lid_t + 1], r - wall / 2 + rebate);
    // USB-C slot, near end (x = 0), level with the receptacle on top of the board
    translate([-1, cy - usb[0] / 2, shelf_z + board[2] - 0.2]) cube([wall + 2, usb[0], usb[1]]);
    // HY2.0 cable slot, far end (x = max): 4-wire lead to the Unit IR
    translate([outer[0] - wall - 1, cy - 3, outer[2] - lid_t - 3.2]) cube([wall + 2, 6, 3.3]);
  }
  // pouch channel rails (no sharp edges: chamfered tops)
  for (y = [cy - batt[1] / 2 - 1.2, cy + batt[1] / 2 + 0.2])
    translate([pouch_x0, y, floor_t]) hull() {
      cube([batt[0], 1.0, 1.4]);
      translate([0, 0.25, 0]) cube([batt[0], 0.5, 2.0]);
    }
  // hard spacer wall: pouch can't slide into the USB-C cutout
  translate([spacer_x, cy - batt[1] / 2 - 1.2, floor_t]) cube([spacer_t, batt[1] + 2.4, batt[2]]);
  // corner columns: top at shelf_z - pad thickness, the soft pads bring the board to shelf_z
  for (p = corner_xy) translate([p[0], p[1], floor_t]) cube([pad[0], pad[1], shelf_z - pad[2] - floor_t]);
}

module lid() {
  plate = [inner[0] + 2 * rebate - 2 * fit, inner[1] + 2 * rebate - 2 * fit];
  li = [inner[0] - 2 * fit, inner[1] - 2 * fit];
  rbox([plate[0], plate[1], lid_t], r - wall / 2 + rebate);
  translate([rebate, rebate, lid_t]) difference() {
    rbox([li[0], li[1], lip_h], r - wall / 2);
    translate([1.2, 1.2, -1]) rbox([li[0] - 2.4, li[1] - 2.4, lip_h + 2], max(0.5, r - wall / 2 - 1.2));
    // notch over the USB-C receptacle (it stands taller than the lip clearance)
    translate([-1, cy - wall - usb[0] / 2 - 0.5, -1]) cube([4, usb[0] + 1, lip_h + 2]);
  }
}

module pads() {
  for (i = [0:3]) translate([i * (pad[0] + 2), 0, 0]) cube(pad);
}

if (part == "base") base();
else if (part == "lid") lid();
else if (part == "pads") pads();
else { base(); translate([0, outer[1] + 5, 0]) lid(); translate([0, -pad[1] - 5, 0]) pads(); }
