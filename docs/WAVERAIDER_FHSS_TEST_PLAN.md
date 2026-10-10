# WaveRaider × ELRS hop fingerprint — hands-on test plan

Everything below was dry-run end-to-end on 2026-10-09 with the **controller off**, using the
replay path. The only step that still needs the drone powered is §3 (live dwell).

**What this feature does:** reads the ELRS frequency-hopping *sequence* off the air and reports a
per-link identifier. It never decodes a payload, so it is unaffected by the undocumented SX1280
`CR_LI_4_8` payload coding that blocks the packet route (notes §30).

**What it does NOT do:** identify an aircraft. It identifies a radio *link*. A quiet receiver does
not establish that nothing is present.

---

## 1. Start the server

```sh
cd ~/Drone_Project/elrs-sdr-transfer/waveraider
export WAVERAIDER_TARGETS=/tmp/wr/targets.json \
       WAVERAIDER_MONITORING=/tmp/wr/monitoring.json \
       WAVERAIDER_SESSIONS=/tmp/wr/sessions
mkdir -p /tmp/wr
python3 -c "import sys; sys.path.insert(0,'.'); from waveraider.web import serve; serve(port=8913, host='127.0.0.1')"
```

Then open <http://127.0.0.1:8913/>.

> There is no `wr` console script in this checkout, which is why the one-liner above is used
> instead of `wr serve`.

---

## 2. Replay first — no drone needed

This proves the whole chain before any RF is involved. In the browser it is the **📡 ELRS hop
fingerprint** button in the Inspect RF drawer (that one dwells live); for replay use the API:

```sh
H='-H X-WaveRaider-Request:1 -H Sec-Fetch-Site:same-origin'
curl -s $H "http://127.0.0.1:8913/api/elrs/fhss?file=sync_ch40_20261009_161112.complex8&seed=1120344" | python3 -m json.tool
```

**Expected** (measured, not predicted):

| field | value |
|---|---|
| `source` | `replay` |
| `n_bursts` / `n_dwells` | 751 / 106 |
| `hop_interval_ms` | 26.6642 — exactly 4 packets at 150 Hz |
| `sync.channel` | **41** |
| `sync.elrs_generation` | `elrs<=3.x ((freq_count / 2) + 1)` |
| `absolute` | `true` |
| `n_positions` | 32 of 240 |
| `net_id` | **`00111858`** |
| `link_id` | `00111858` (same as net_id) |
| `evidence` | `VALIDATED` |
| `seed_check` | 31/31, rotation 80 |

Three checks worth running, because each one caught a real bug during the dry run:

```sh
# (a) the SAME link on a second capture must give the SAME net_id
curl -s $H ".../api/elrs/fhss?file=sync_ch40b_20261009_162100.complex8&seed=1120344"
#     -> net_id 00111858 again (29/30). NOTE fingerprint_digest DIFFERS between
#        the two — that is per-capture by design and is not an identifier.

# (b) a WRONG seed must be rejected, not accepted
curl -s $H ".../api/elrs/fhss?file=sync_ch40b_20261009_162100.complex8&seed=1120345"
#     -> seed_check 3/30, match false, net_id absent

# (c) path traversal must be refused
curl -s $H --get --data-urlencode "file=../AGENTS.md" ".../api/elrs/fhss"
#     -> 400 "file must be a regular file under captures/"
```

---

## 3. Live dwell — needs the controller ON

Either press **📡 ELRS hop fingerprint** in Inspect RF, or:

```sh
curl -s $H "http://127.0.0.1:8913/api/elrs/fhss?dwell_s=12&seed=1120344" | python3 -m json.tool
```

Takes ~20 s (12 s dwell + analysis) and holds the radio for the whole dwell.

**Centre on the sync channel (2440.4 MHz, the default).** That is not cosmetic: the sync channel is
pinned to the start of every 80-hop block and is the only channel recurring at exactly 80-hop
spacing, which is what yields *absolute* sequence positions and self-calibrates the channel index.
A dwell elsewhere in the band still fingerprints, but only up to an unknown rotation
(`absolute: false`), and cannot drive the seed search.

**With the controller OFF** the honest result is `n_bursts: 0`, `evidence: NONE`, and a note that
this does not establish absence. That is what the first live test returned at 23:26, and the
capture confirmed it: mean power −60 dB, zero bursts at every gate from 45 dB down to 15 dB.

---

## 4. If the live dwell finds nothing while the controller IS on

In order of likelihood:

1. **Wrong band/centre.** Confirm `center_freq_hz` is 2.4404e9 in the response.
2. **Gate at the floor.** `snr_gate_db: 25.0` means the frame-SNR tail never rose above the floor —
   i.e. nothing strong in the dwell. A good capture gates at ~47 dB.
3. **Too short a dwell.** One 240-hop sequence period is ~6.4 s; the 12 s default covers ~1.9
   periods. Raise with `dwell_s` (1–60 allowed).
4. **Antenna/gain.** Try `gain=50`.

Every live dwell is written to `captures/fhss_<timestamp>/`, so a disappointing live run can be
re-analysed offline with `file=` without re-flying anything.

### Disk and git cost — read this before pressing the button repeatedly

A 12 s dwell at 10 MS/s is **~480 MB**, and this repo's **pre-push hook auto-commits `captures/`**,
so every dwell otherwise lands in git history permanently (`.git` is already ~6 GB). Pass **`keep=0`**
to analyse the dwell and then discard the IQ:

```sh
curl -s $H "http://127.0.0.1:8913/api/elrs/fhss?dwell_s=12&seed=1120344&keep=0"
#   -> iq_kept: false, iq_file: null
```

Keeping it is still the default, because offline re-analysis via `file=` is the main way to debug a
dwell that found nothing. Use `keep=0` for demo presses where you only want the answer, and the
default when the dwell is worth keeping.

---

## 5. Recovering a net_id for a *new* link

`seed=` only *confirms* a seed you already have (~1 ms). Discovery is a ~90-minute sweep of the
2^31 space and is an offline job:

```sh
cd ~/Drone_Project/elrs-sdr-transfer
python3 fhss_seed_scan.py captures/<a>.complex8 captures/<b>.complex8 31
```

Pass **two or more captures of the same link** — it keeps only the sequence positions all of them
agree on. That matters: a single capture's fold once emitted a structurally impossible constraint
(two positions claiming the same channel inside one 80-hop block) and silently poisoned a full
90-minute sweep (notes §33c).

Both captures must contain the pinned sync channel, i.e. be centred near 2440.4 MHz.

---

## 6. Current limits, stated plainly

- **`fingerprint_digest` is per-capture, not an identifier.** It hashes whichever positions that
  dwell recovered, so the same link digests differently on different dwells. The stable identifier
  is `net_id` (the seed). The UI labels this.
- **No stable ID without a seed.** Without `seed=`, `link_id` is `null` by design — there is no
  honest way to match two captures from one dwell's hash alone.
- **ELRS is `SPECIFIC`, not `IDENTIFIED`**, in `protocol_catalog.py`. The seed recovers UID[2..5]
  only (and UID[2] only to a single bit), and per AGENTS.md §1 the bind UID is a pairing secret
  rather than a hardware identity.
- **2.4 GHz ELRS only.** The sync-channel logic is keyed to the 80-channel ISM2G4 map.
- **The radio is single-access.** The route serialises on the same device lock as ELRS Find and
  Inspect RF, so a dwell blocks a concurrent sweep rather than corrupting it.

---

## 7. Test status

- WaveRaider suite: **621 passed, 4 failed, 3 skipped.** The 4 are pre-existing
  `test_remote_id_reference` failures from missing `opendroneid-core-c` fixture files, unrelated to
  this work.
- `tests/test_elrs_fhss_route.py` (10 tests) covers: the route is gated against cross-site use
  (derived by parsing `web.py`, not a hand-maintained list), no stale gate entries, the TX gate,
  the UI control is wired through `specApi`, the wording discipline, the evidence label, and the
  per-capture-digest-vs-stable-id distinction.
- `elrs_sdr.selftest`: ALL CHECKS PASSED. `core.fhss_observe`, `core.fhss_seed`, `core.lora_sync`,
  `core.lora_ref` selftests all pass.
