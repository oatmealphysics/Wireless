# ELRS SDR — Conversation Log (Sept 30, 2026)

A verbatim-style transcript of the session where we picked up the `elrs-sdr`
work, verified the shipped tarball, re-delivered it over GitLab, discussed
Python venvs, and hit the UHD import issue.

---

## 1. Picking up where we left off

**User:** Good morning, where did we leave off?

**Assistant (me):** Recapped the last session:
- Built `elrs-sdr` — a Python + UHD tool to capture/decode ELRS drone RF with
  your B205mini, at `~/repos/elrs-sdr/`.
- Protocol layer done & self-tested: LoRa chirp demod, CRC-16/14 with the
  UID-derived init, FHSS LCG + hop table, 2.4 GHz 6-rate table, OTA packet
  model.
- Hardware path validated on your box (UHD 4.9 pybind, B205mini initializes
  clean, `recv` returns real samples once `set_subdev_spec`/`set_antenna` are
  done).
- Packaged a clean source-only `~/elrs-sdr.tgz` (24 KB, `.venv` excluded).
- The open question you'd pushed on: identifying a drone **in the air** by a
  hardware-unique ID (MAC/IMEI-style) — ELRS doesn't transmit one, so the real
  targets are Wi-Fi MAC/BSSID, BLE address, and ODID/Remote ID serial.

Offered two threads: (1) close out the real-RF capture/decode, or (2) pivot to
the identification track.

**User:** Let's start with 1.

**Assistant (me):** Verified the artifact on a clean extraction of the shipped
tarball (selftest ALL PASS, CLI `--help` loads, `info` fails gracefully with
no radio). Confirmed the install path. Asked how you'd get the tarball onto
the laptop / whether PyPI is reachable.

**User:** Laptop has internet (pip install numpy works normally).

**Assistant (me):** Rebuilt the tarball, re-verified. Gave you the artifact:
- Path `/home/andeds-agent/elrs-sdr.tgz`, SHA-256 `3ca0445f…`.
- Install: `python3 -m venv .venv` → `pip install numpy` → `pip install -e .`
  → `selftest` → `info`.
- Caveat: on the enclave box, `pip install` kept getting hijacked by the proxy
  var, so the clean re-verification was interrupted.

---

## 2. Delivery over GitLab (scp didn't work)

**User:** scp won't work still, put it in the github [GitLab].

**Assistant (me):** Re-used the existing transfer project **516**
(`andeds-agent/elrs-sdr-transfer`, visibility `internal`), pushed the new
`elrs-sdr.tgz` via the commits API (commit `b5ff743`), and verified with a
SHA-256 round-trip (local == server, MATCH).

Hand-off to you:
```
cd elrs-sdr-transfer
git pull
tar xzf elrs-sdr.tgz
```
Then:
```
cd repos/elrs-sdr
python3 -m venv .venv
.venv/bin/pip install numpy
.venv/bin/pip install -e .
.venv/bin/python -m elrs_sdr.selftest
.venv/bin/python -m elrs_sdr info
```
Raw-file fallback (if no clone):
```
curl -sk -o elrs-sdr.tgz "https://gitlab01.an.dd/api/v4/projects/516/repository/files/elrs-sdr.tgz/raw?ref=main" -H "PRIVATE-TOKEN: <your-PAT>"
```

---

## 3. Why a venv?

**User:** What is the point of entering a Python venv? I've never understood
why you do that.

**Assistant (me):** Explained what a venv is (a dir with its own Python +
site-packages), the general reasons (isolation, per-project pinning,
reproducibility, no sudo, clean breaks), and the honest caveat that on a
personal laptop the venv is mostly a convenience/safety wrapper — you could
just `pip3 install numpy` globally. Offered a venv-free path.

**User:** venv free

**Assistant (me):** Gave the no-venv path:
```
pip3 install numpy
pip3 install -e /path/to/repo/repos/elrs-sdr
python3 -m elrs_sdr info
```
Plus the `--break-system-packages` / `uv pip install --system` fallbacks for
externally-managed Pythons, and two diagnostics if `import` errors:
```
python3 -c "import sys,numpy;print(sys.executable,numpy.__version__)"
python3 -c "import uhd;print(uhd.get_version_string())"
```

---

## 4. The UHD issue

**User:** I'm having a UHD issue again. `./elrs-sdr scan` →
`error: 'uhd' python module not found. Install UHD with python bindings on the
machine with the radio attached.`

**Assistant (me):** Confirmed the tool just does `import uhd`. Asked you to run
(one-liners, on the laptop) and paste back:
1. `python3 -c "import sys;print(sys.executable)"`
2. `python3 -c "import uhd;print(uhd.get_version_string())"`
3. `find / -name "uhd*.so" 2>/dev/null | head`
4. `python3 -c "import gnuradio.uhd as u;print(u.get_version_string())"`
5. `find / -name "types.py" -path "*uhd*" 2>/dev/null | head`

Two leading suspects:
- **`.pth` path issue (most likely):** UHD 4.9's build drops the module in
  `/usr/local/lib/python3.X/site-packages/`, which is NOT on Ubuntu's default
  `sys.path` (it uses `dist-packages`). Fix:
  `echo /usr/local/lib/python3.12/site-packages | sudo tee /usr/lib/python3/dist-packages/uhd49.pth`
  (match the exact dir your `find` returns).
- **Stale `uhd.py` shim** shadowing the real module (pointing at the 4.6
  `gnuradio.uhd`) — `rm` it if that's what you see.

**User:** Can you put this conversation into a markdown file and upload it to
the git, I want to read through it all.

**Assistant (me):** *(writing this file and pushing it to GitLab project 516.)*

---

## Open items
- **UHD import** on the laptop: pending your 5 diagnostic outputs.
- **Real-RF capture/decode:** not yet run live (needs the B205mini + working
  `import uhd`).
- **Identification track (thread 2):** Wi-Fi OFDM → MAC stage 2, and the
  ODID/Remote ID companion track — not started.

## Artifact references
- Tarball: `elrs-sdr.tgz` (24 KB, source-only), SHA-256
  `3ca0445f69fdab7aec21ac54b7fecd5cdf800cb1a4365776e332f5ae9f50ea2e`
- GitLab project: `andeds-agent/elrs-sdr-transfer` (id 516, internal)
- Commit: `b5ff743` — "elrs-sdr source tarball (clean, verified) 2026-09-30"
