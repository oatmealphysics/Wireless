# Spectrum and experimental DroneID

[Back to README](../README.md)

Inspect RF is an advanced inspection tool in the Remote ID first roadmap.
It is available today, separately from recorded Remote ID decoding. Native
bladeRF and RTL-SDR backends require their respective drivers/tools; Python
SDR support also uses the `sdr` extra. Docker does not configure radio access.

**Inspect RF.** A live power-vs-frequency plot and scrolling waterfall for the
drone bands only. It opens as a drawer on Monitor, from an observation or from a
radio under Settings › Receivers, and starts on the 2.4 GHz band.

| Band tab | Range | Typical use |
| --- | --- | --- |
| 868 ctrl | 850–870 MHz | Control and telemetry links (EU; Crossfire reaches down to 850 MHz) |
| 900 ctrl | 902–928 MHz | Control and telemetry links (US) |
| 1.3 video | 1080–1360 MHz | Analog FPV video |
| 2.4 GHz | 2400–2483.5 MHz | Control, telemetry and digital video; shared with Wi-Fi |
| 5.2 GHz | 5150–5645 MHz | Digital links such as Autel SkyLink and DJI O4 (5.15–5.25 GHz), and 5 GHz Wi-Fi drones; shared with Wi-Fi |
| 5.8 video | 5645–5945 MHz | FPV and digital video; overlaps 5 GHz Wi-Fi |

Together the bands cover every link in Wave Raider's RF profile reference; they are
not a guarantee that every drone, region or future link mode is covered.

The band tab shows which band is in view. Center and span fine-tune inside that
band: a center outside the drone bands moves to the nearest band, and the span is
capped to the band (and to 60 MHz), so the view never leaves the drone bands.
When **Tune** moves what you typed, a note under the band tabs says what is shown
instead. The server applies the same limits to every sweep. An older saved view
outside the drone bands is moved into one. Band names say where a frequency sits,
not what is transmitting. 1.3, 5.2 and 5.8 GHz need an SDR that reaches them, such
as a bladeRF.

Tuning in, or clicking a peak, in the 1.3 or 5.8 GHz video band checks it for
analog video sync with a live wideband SDR. The check reports video only when the
FM-demodulated signal shows the NTSC (15,734 Hz) or PAL (15,625 Hz) line rate within
±0.2% together with a matching harmonic. This greatly reduces reports from noise and
off-standard rates in testing, but interference with the same tones and harmonics can
still be reported as video. It is a signal hint, not a drone identification, and it has
not been validated on live radio captures. Retuning or changing radio clears
results for the previous view. There is no audio listening.
When an RTL-SDR-class device is connected and `rtl_power` is installed it shows
live RF; otherwise it shows a clearly-labeled simulated spectrum so the view and
tuning work with no hardware. While it's live, the view takes **exclusive
control of the SDR** — device access is serialized, no other consumer can grab
the radio, and it only ever sweeps the tuned range. Control is released when you
stop or close the drawer (and auto-expires if the browser tab goes away), and
Settings › Receivers shows whether a radio is currently held.

**Calmer Spectrum display.** Display pace defaults to **Steady**: the smoothed
plot/waterfall update at most once a second, signal rows refresh every five
seconds, and the gold trace holds peaks for five seconds before decaying.
**Slow** uses two-second plot updates, ten-second signal rows, and eight-second
peak hold. **Live** shows every sweep. These are maximum display rates; a slower
radio still determines when new data arrives. This changes presentation only,
not capture timing. Display pacing is remembered on this browser.

Rows are ordered by frequency and stay still while hovered or keyboard-focused.
Their timestamp marks the last displayed snapshot, not a continuous reception
history. **Hold display** freezes the plot, waterfall and list while capture
continues; **Resume display** shows the latest data. Retuning or changing
radio clears the old display hold immediately. **CSV (raw)** exports the latest
unsmoothed sweep; **PNG** saves the displayed plot. Slower display can hide short
changes in the list; use Live or the peak-hold trace when watching transients.

**Spectrum signals are not drone counts.** Frequency and approximate bandwidth
cannot identify a drone, especially in shared Wi-Fi bands. Spectrum peaks remain
unidentified and receive no drone badge from a band match. The API retains
compatible protocol names under `rf_candidates` as reference hints only; the
legacy `drone` badge list is empty for power-only sweeps. Neither a matching
profile nor its catalog tier is detection evidence. Use decoded Remote ID
packets or the separate experimental pilot-pair scan for stronger evidence;
neither a missing badge nor a negative short scan proves no drone is present.

Signal rows show these neutral hints as **Possible RF profiles on this band**,
alongside **Unidentified — no drone-specific evidence**. Hover a hint to see the
backend's explanation. A separate drone-evidence badge requires a nonempty
evidence list; reference profiles never populate that badge.

**Experimental DroneID scan.** The scan uses the Spectrum tuner center, which
must be within 2400–2483.5 MHz or 5725–5850 MHz. Other centers are refused before
capture. The API requires an explicit, finite center in Hz. Each capture uses
15.36 MSPS (center ±7.68 MHz), independent of the displayed span; results show
the captured frequency range. Band-edge centers may capture beyond the band.
A 10,752,000-sample capture lasts 700 ms, longer than the roughly 600 ms between
DroneID bursts, so one capture spans at least one burst interval; analysing it
takes a few seconds. Continuous monitoring is not implemented.

**Detection limits.** The wideband scan checks for
both DJI Zadoff-Chu pilots in the correct order and two-symbol spacing; the
ordered pair is the evidence. The cyclic-prefix (OFDM) metric is still reported
but no longer has to pass first, because the pilots stay detectable in noise that
hides it. The pilots are searched over carrier offsets from −22.5 to +22.5 kHz in
7.5 kHz steps (about ±26 kHz of combined drone and receiver crystal error); the
result reports the offset used as `cfo_hz`. A match
is labeled **DroneID candidate**, not a confirmed identity: no payload or CRC
has been decoded, and no drone/operator coordinates are extracted by this scan.
This is separate from the Remote ID packet decoder. Isolated
pilots, generic OFDM, and periodic carriers do not establish DroneID. The current
matched-filter references require 15.36 MSPS. One real recording (a DJI Mini 4K,
from the WaveShark project) has been checked: all 13 bursts are candidates, also
with a further +12/−18 kHz offset applied. Eight pinned Wi-Fi, LTE, BLE, ExpressLRS,
Remote ID and analog-video recordings produced no candidates across nine windows
(2.15 seconds of source data); see [validation provenance](../tests/fixtures/droneid/PROVENANCE.md).
These short recordings are regression checks, not a measured field false-alarm rate. That is one aircraft model on
one receiver; other models, multipath and interference remain unvalidated. A scan
can still miss a burst, so an empty result does not establish absence.
Implementation reference: [proto17/dji_droneid](https://github.com/proto17/dji_droneid).

