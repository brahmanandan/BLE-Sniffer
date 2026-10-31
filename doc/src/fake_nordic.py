#!/usr/bin/env python3
"""Run the bridge against a fake port that looks like the Nordic sniffer image (USB 1915:522A).
Used only to screenshot the "sniffer image" banner for the manual.

    python -I doc/src/fake_nordic.py <repo root> <reports dir>      # serves http://localhost:8015
"""
import sys
sys.path.insert(0, sys.argv[1])
from backend import bridge as B
P = type("P", (), {"device": "/dev/ttyACM0", "vid": 0x1915, "pid": 0x522A})()
B.Bridge.candidate_ports = lambda self: [P]
B.list_ports.comports = lambda: [P]
sys.argv = ["bridge", "--http-port", "8015", "--reports", sys.argv[2]]
B.main()
