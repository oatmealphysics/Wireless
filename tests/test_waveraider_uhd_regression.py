"""Targeted regression: the spectrum.py assertions my UHD wiring could affect.
Run directly (no pytest). Replicates the relevant WaveRaider test_spectrum.py
cases with a hand-rolled monkeypatch, and ADDS the new expectation: a live UHD
backend is now ACCEPTED by scan_droneid / analyze_signal (previously only
bladerf was)."""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "/home/andeds-agent/repos/elrs-sdr")
sys.path.insert(0, "/home/andeds-agent/repos/elrs-sdr/waveraider")

from waveraider import spectrum  # noqa: E402

PASS, FAIL = 0, 0
def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else: FAIL += 1; print(f"  FAIL  {name}  {detail}")

def mp(obj, attr, val):
    orig = getattr(obj, attr)
    setattr(obj, attr, val)
    return orig


# --- a fake live source_status with a chosen backend --------------------
def fake_status(backend):
    return lambda selected=None: {"mode": "live", "device": "USRP",
                                  "backend": backend, "note": "live"}

print("== DroneID gate: the two pre-existing expectations still hold ==")
# 1. no SDR -> unavailable
orig_ss = mp(spectrum, "source_status", lambda selected=None:
             {"mode": "simulated", "device": None, "backend": None, "note": "no sdr"})
r = spectrum.scan_droneid()
ok("no SDR -> unavailable + 'SDR' in note", r["available"] is False and "SDR" in r["note"], str(r))

# 2. live RTL (narrowband) -> refused, 'rtl_power' in note  (pre-existing test)
mp(spectrum, "source_status", fake_status("rtl_power"))
r = spectrum.scan_droneid()
ok("live rtl_power -> refused + 'rtl_power' in note",
   r["available"] is False and "rtl_power" in r["note"], str(r))
spectrum.source_status = orig_ss

print("== NEW: a live UHD backend is now ACCEPTED by scan_droneid ==")
# With backend 'uhd', the gate must NOT refuse (previously only 'bladerf' passed).
# It will then fail at capture (no real device) -> 'wideband capture failed',
# which proves the gate let it through rather than refusing the backend.
mp(spectrum, "source_status", fake_status("uhd"))
try:
    import waveraider.uhd_backend as ub
    ub._DEV = None; ub._BACKEND = None
    # force the backend to report no native uhd and no soapy -> capture returns None
    import elrs_sdr.radio as radio
    radio.uhd = None
    r = spectrum.scan_droneid()
    # It must NOT be the 'backend can't reach that' refusal; it should have
    # reached the capture step.
    refused_backend = "can't reach that" in r.get("note", "")
    ok("live UHD -> gate ACCEPTS (not 'can't reach that' refusal)",
       not refused_backend, str(r))
finally:
    spectrum.source_status = orig_ss

print("== analyze_signal: UHD backend now has a capture path (not 'not wired') ==")
# Pre-change, a non-bladerf/rtl backend returned 'Live demod isn't wired'.
# Now 'uhd' has a capture path; with no device it returns a capture-failed note,
# not the 'isn't wired' refusal.
mp(spectrum, "source_status", fake_status("uhd"))
try:
    import waveraider.uhd_backend as ub
    ub._DEV = None; ub._BACKEND = None
    import elrs_sdr.radio as radio
    radio.uhd = None
    r = spectrum.analyze_signal(2_400_000_000)
    not_wired = "isn't wired for the uhd" in r.get("note", "")
    ok("analyze_signal UHD -> not the 'isn't wired' refusal", not not_wired, str(r))
finally:
    spectrum.source_status = orig_ss

print("== pre-existing: sweep shape / band-hint honesty unaffected ==")
# A simulated sweep (no SDR) still has the right shape and keeps drone=[] hints.
mp(spectrum, "source_status", lambda selected=None:
   {"mode": "simulated", "device": None, "backend": None, "note": "simulated"})
d = spectrum.sweep(2_400_000_000, 40_000_000, 256)
ok("simulated sweep has 256 points", len(d["points"]) == 256)
ok("simulated sweep signals keep drone=[] (no over-claim)",
   all(s["drone"] == [] for s in d["signals"]))
ok("simulated sweep reports a floor + peak", "floor_db" in d and "peak" in d)
spectrum.source_status = orig_ss

print(f"\n== {PASS} passed, {FAIL} failed ==")
sys.exit(1 if FAIL else 0)
