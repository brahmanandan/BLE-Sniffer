"""
report.py — test-run report model and JSON / printable HTML export.

The web app's test runner posts a result object:

    {"title": "...", "started": <ms>, "finished": <ms>, "jig": {...}, "dut": {...},
     "steps": [{"id":"keys","name":"Keys","status":"pass|fail|skip","detail":"...",
                "metrics": {...}, "started": <ms>, "finished": <ms>}, ...]}

save() writes report_<stamp>.json and report_<stamp>.html into the reports dir.
"""
from __future__ import annotations

import html
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("pass", "fail", "skip", "error")


@dataclass
class Step:
    id: str
    name: str
    status: str = "skip"
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    started: float | None = None
    finished: float | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        st = str(d.get("status", "skip")).lower()
        return cls(id=str(d.get("id", d.get("name", "step"))), name=str(d.get("name", d.get("id", "step"))),
                   status=st if st in STATUSES else "error", detail=str(d.get("detail", "")),
                   metrics=dict(d.get("metrics") or {}), started=d.get("started"), finished=d.get("finished"))


@dataclass
class Report:
    title: str = "Remote test report"
    started: float | None = None
    finished: float | None = None
    jig: dict[str, Any] = field(default_factory=dict)
    dut: dict[str, Any] = field(default_factory=dict)
    operator: str = ""
    notes: str = ""
    steps: list[Step] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        return cls(title=str(d.get("title") or cls.title), started=d.get("started"), finished=d.get("finished"),
                   jig=dict(d.get("jig") or {}), dut=dict(d.get("dut") or {}), operator=str(d.get("operator", "")),
                   notes=str(d.get("notes", "")), steps=[Step.from_dict(s) for s in d.get("steps") or []])

    @property
    def verdict(self) -> str:
        ran = [s for s in self.steps if s.status != "skip"]
        if not ran:
            return "incomplete"
        return "pass" if all(s.status == "pass" for s in ran) else "fail"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        d["summary"] = {k: sum(1 for s in self.steps if s.status == k) for k in STATUSES}
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def to_html(self) -> str:
        e = html.escape

        def ts(ms):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000)) if ms else "—"

        def kv(d):
            if not d:
                return "<span class=muted>—</span>"
            out = []
            for k, v in d.items():
                if isinstance(v, dict) and v:      # e.g. the remote's identity: one line per field
                    out.append("<div><b>%s</b>:<div style='margin-left:14px'>%s</div></div>" % (e(str(k)), kv(v)))
                else:
                    out.append("<div><b>%s</b>: %s</div>" % (e(str(k)), e(fmt(v))))
            return "".join(out)

        def fmt(v):
            if isinstance(v, bool):
                return "yes" if v else "no"
            if isinstance(v, float):
                return "%.1f" % v
            if isinstance(v, dict):
                return " · ".join("%s %s" % (k, fmt(x)) for k, x in v.items()) or "—"
            if isinstance(v, list):
                return ", ".join(fmt(x) for x in v) or "—"
            return str(v)

        rows = []
        for i, s in enumerate(self.steps, 1):
            dur = "%.1f s" % ((s.finished - s.started) / 1000) if s.started and s.finished else "—"
            rows.append(
                "<tr><td>%d</td><td>%s</td><td><span class='badge %s'>%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (i, e(s.name), e(s.status), e(s.status.upper()), e(s.detail), kv(s.metrics), dur))
        v = self.verdict
        return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
 body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111;margin:24px;max-width:1000px}
 h1{font-size:22px;margin:0 0 4px} .muted{color:#666}
 .verdict{display:inline-block;padding:6px 14px;border-radius:6px;font-weight:700;font-size:18px;margin:12px 0}
 .verdict.pass{background:#d9f2df;color:#0b6623}.verdict.fail{background:#fbdada;color:#9b1111}
 .verdict.incomplete{background:#eee;color:#444}
 .meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:12px 0 20px}
 .meta section{border:1px solid #ddd;border-radius:6px;padding:10px}
 .meta h3{margin:0 0 6px;font-size:13px;text-transform:uppercase;color:#555}
 table{border-collapse:collapse;width:100%%} th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
 th{background:#f4f4f4} .badge{padding:2px 8px;border-radius:4px;font-weight:600;font-size:12px}
 .badge.pass{background:#d9f2df;color:#0b6623}.badge.fail,.badge.error{background:#fbdada;color:#9b1111}
 .badge.skip{background:#eee;color:#555}
 @media print{body{margin:10mm}}
</style></head><body>
<h1>%(title)s</h1>
<div class="muted">Started %(started)s · Finished %(finished)s</div>
<div class="verdict %(v)s">%(V)s</div>
<div class="meta">
 <section><h3>Jig</h3>%(jig)s</section>
 <section><h3>Device under test</h3>%(dut)s</section>
 <section><h3>Summary</h3>%(summary)s</section>
</div>
<table><thead><tr><th>#</th><th>Step</th><th>Result</th><th>Detail</th><th>Metrics</th><th>Time</th></tr></thead>
<tbody>%(rows)s</tbody></table>
%(notes)s
</body></html>
""" % {"title": e(self.title), "started": ts(self.started), "finished": ts(self.finished), "v": v, "V": v.upper(),
       "jig": kv(self.jig), "dut": kv(self.dut),
       "summary": kv({k: n for k, n in self.to_dict()["summary"].items() if n} | ({"operator": self.operator} if self.operator else {})),
       "rows": "".join(rows), "notes": "<h3>Notes</h3><p>%s</p>" % e(self.notes) if self.notes else ""}

    def save(self, directory: Path) -> dict[str, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime((self.finished or time.time() * 1000) / 1000))
        base = directory / ("report_%s" % stamp)
        n = 1
        while base.with_suffix(".json").exists():
            base = directory / ("report_%s_%d" % (stamp, n))
            n += 1
        jp, hp = base.with_suffix(".json"), base.with_suffix(".html")
        jp.write_text(self.to_json())
        hp.write_text(self.to_html())
        return {"json": jp, "html": hp}
