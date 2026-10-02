# gr-ieee802-11 with json_sink — build & run

This is the [gr-ieee802-11](https://github.com/bastibl/gr-ieee802-11) source
(vendored, trimmed) **plus a `json_sink` block** that taps the decoded
802.11 frames and prints one JSON line per frame to stdout. That's the seam
the elrs-sdr webui reads to populate the Wi-Fi MAC column.

Added files:
- `include/ieee802_11/json_sink.h`
- `lib/json_sink_impl.h` / `lib/json_sink_impl.cc`
- `python/bindings/json_sink_python.cc`
- `grc/ieee802_11_json_sink.block.yml`
- `apps/wifi_json_rx.py` — **headless** flowgraph (no GUI)
- `apps/run_wifi_rx.sh` — GUI-flowgraph wrapper (alternative)

Already-registered in: `lib/CMakeLists.txt`, `python/bindings/CMakeLists.txt`,
`python/bindings/python_bindings.cc`, `grc/CMakeLists.txt`.

## Build (on the laptop, with GNU Radio 3.10 installed)

```
cd gr-ieee802-11
# build its dependency first (gr-foo) — see apps/notes, or install gr-foo
git clone https://github.com/bastibl/gr-foo.git   # if not already
cd gr-foo && mkdir -p build && cd build && cmake .. && make -j$(nproc) && sudo make install && sudo ldconfig && cd ../../..

cd gr-ieee802-11
mkdir -p build && cd build
cmake ..
make -j$(nproc)
sudo make install
sudo ldconfig
sudo sysctl -w kernel.shmmax=2147483648
volk_profile
```

## Verify the demod works (NO hardware, NO SDR)

```
cd ../examples
python3 wifi_loopback.py
```
Decoded packets on the console = the whole 802.11 PHY chain is correct.

## Run it on the B205 (headless, JSON to stdout)

```
cd gr-ieee802-11/apps
python3 wifi_json_rx.py --freq 2437e6 --srate 20e6 --gain 40 --channel 6
```

Each decoded frame prints one line like:
```
{"ts":1790950000.123,"ch":6,"freq_mhz":2437.0,"fc":128,"type":0,"subtype":8,"bssid":"f2:a7:31:25:30:26","src":"f2:a7:31:25:30:26","dst":"ff:ff:ff:ff:ff:ff","ssid":"..."}
```

- `type:0 subtype:8` = beacon; `bssid` = the AP's hardware MAC (your end goal).
- Check the `bssid` against your `iw dev wlp0s20f3 scan` ground truth.

### Notes
- **20 Msps over USB 2.0** on the B205 mini delivers ~8 MS/s — the flowgraph
  uses the *requested* rate for its internal timing, which is fine for 802.11a/g
  (the OFDM symbol period is set by the PHY, not the capture rate). If you see
  it under-run, try `--srate 10e6`.
- One UHD process owns the B205 at a time — don't run the Python `elrs-sdr
  survey` at the same time.
