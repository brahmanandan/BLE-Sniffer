# doc/src — regenerating the manual's figures and screenshots

| File | Produces |
|---|---|
| `make_diagrams.py` | `doc/*.svg` — every diagram, drawn from one palette and drawing kit |
| `render.sh` | `doc/*.png` from the SVGs (headless Chrome, 2×) |
| `screenshots.py` | `doc/images/*.png` — drives the real web app in Chrome against the simulator |
| `fake_nordic.py` | a bridge that sees a fake Nordic-sniffer port, for the "sniffer image" screenshot |
| `make_pdf.py` | `doc/Remote-Test-Jig-User-Manual.pdf` — the whole manual as one PDF |

## PDF of the whole manual

```bash
python3 doc/src/make_pdf.py                  # -> doc/Remote-Test-Jig-User-Manual.pdf
python3 doc/src/make_pdf.py -o manual.pdf    # somewhere else
```

Needs `pandoc` and Google Chrome/Chromium (no LaTeX). A4, cover page, clickable contents, each chapter
on a new page; links between chapters become links inside the PDF. Rebuild it after editing any chapter.

## Diagrams

```bash
python3 doc/src/make_diagrams.py     # edit the functions in this file to change a diagram
doc/src/render.sh                    # all PNGs; or: doc/src/render.sh doc/pairing-flow.svg
```

## Screenshots

No hardware needed. From the repo root:

```bash
.venv/bin/python backend/bridge.py --simulate --http-port 8014 --reports /tmp/simreports &
.venv/bin/python -I doc/src/fake_nordic.py "$PWD" /tmp/simreports &
.venv/bin/python doc/src/screenshots.py doc/images /tmp/chrome-doc-profile
# then stop both bridges (pgrep -af backend/bridge.py / fake_nordic, kill <pid>)
```

The simulator's remote returns clearly simulated identity values (manufacturer "Simulated", model
"SIM-RCU-1"), a typical report map, keys, voice and IR, so every screen can be shown populated.
