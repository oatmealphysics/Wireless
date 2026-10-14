# SECURITY.md — agent guardrails for `elrs-sdr`

This file is a **contract between the operator and any AI agent writing code in
this repo** (andeds the orchestrator, Codex the primary coder, Claude the
adversarial QC reviewer — or any
future agent). AI agents are context-driven: they read the repo, follow its
patterns, and generate code that matches the style. A security file is a
*directive for machines* — when an agent sees "secrets never in code," it
follows that rule at the point of generation, before the code reaches a
review. It doesn't replace good security practice; it catches the most common
agent mistakes early.

Read `AGENTS.md` §0 (Authorization & scope) + §2 (The hard boundary) first —
this file is the *code-level* guardrails that operationalize that scope.

---

## The agent rules

### 1. No secrets in code

**Rule one.** Never commit credentials, API tokens, private keys, radio
binding phrases / UIDs used as secrets, database passwords, or any
machine-specific secret. AI agents love to "helpfully" inline a value to get
something working — this rule stops that.

- The ELRS 6-byte **bind UID** is a *pairing secret*, not a hardware ID. It
  must **never** appear as a hardcoded constant in **code, fixtures, logs, or
  tests** (AGENTS.md §1 Identity goal + §41f: ground truth for *validation
  only*, kept out of importable source). A detector that needs the UID takes
  it as a *runtime* input or recovers it from the air — never from a
  constant.
- **Carve-out for analysis prose only.** The handoff logs and protocol
  reconstruction docs (`docs/*.md`, `references/*.md`) may reference the UID
  bytes **for cross-check against captured frames** — this is analysis
  context, not something an agent should ingest and inline into generated
  code. Agents reading these notes to understand the protocol must still take
  the UID as a runtime input in any code they write; "I saw it in the notes"
  is not permission to hardcode it in `.py` / `.ts` / fixtures.
- If a value looks like it should be a secret (a key, a token, a passphrase),
  it goes in a **gitignored local file** (`.env`, `secrets.local`, a capture
  `.meta.txt` that's not committed) — not in a tracked source file.
- Client-side / UI-facing configuration must be **non-sensitive** only
  (display names, thresholds, UI layout). Nothing that grants access or
  identifies a specific link-by-secret goes in the UI bundle.

### 2. Assume the capture data is untrusted input

Any `.complex8` IQ capture, serial-frame log, or `.exe` we parse is
**untrusted input** — it came from a device / the air / a vendor and may be
malformed, truncated, or adversarial. The parsers must:

- **Validate before they parse.** Check the capture length / sample rate /
  frame size *before* indexing into it. A frame that claims 64 bytes but is
  17 bytes must not be sliced as if it were 64. (This is the class of bug the
  EMAX protocol study is probing — the native reader must bounds-check every
  field it reads, exactly like the vendor tool's parser does.)
- **Never trust a length field.** A length/count byte in a frame or capture
  header is data, not instruction. Clamp it to the buffer size before use.
- **Fail closed, not open.** A parse that can't validate (bad CRC, short
  frame, out-of-range index) raises a typed exception (`BadFrame`,
  `ShortFrame`, `CrcMismatch`) or returns an explicit error-carrying result
  (a dataclass with `ok: bool, error: str, value: T | None`). It does NOT
  return a bare `None` the caller can accidentally swallow with
  `if x: ...`, and it does NOT return a partially-filled struct that a
  downstream caller might act on. Pick one of the two patterns per module
  and apply it consistently; don't mix.
- **No eval / no dynamic code from parsed data.** Never `eval`, `exec`,
  `pickle.loads`, or `yaml.load` (unsafe loader) on anything derived from a
  capture or a serial frame. Use `json` / `ast.literal_eval` / the safe
  loaders.

### 3. The TX gate is a staged control, not a wall (updated 2026-10-14)

**Scope framing:** transmitting is a **stated project goal** — the end state
includes a working TX capability. The current **receive-only** posture is a
**phase**, not the mission. So rule 3 is an in-force control **for the current
phase** (bench-only, human sign-off, detector/UI never reach TX), to be
re-evaluated as the TX phase begins — **not** a declaration that TX is out of
scope. The passive-only boundary (AGENTS.md §2) is a *security control for the
current phase*, not a permanent scope limit. The TX path in `elrs_sdr/radio.py`
(`tx_tune` / `tx_stream`) and the CLI `tx` command are **gated** for now. For an
agent, the gate is equivalent to an authorization check:

- **Detection / analysis / UI code must never import, call, or wire in the
  TX side.** A code path that can reach `tx_*` from a sweep, the UI, or a CLI
  flow is a vulnerability in this project's threat model — it converts a
  receive-only tool into an active transmitter. Flag it.
- **The gate covers the pattern, not just the named files.** Rule applies to
  `tx_tune` / `tx_stream` / `elrc_bench_tx.py` AND to any new file matching
  `*_tx*.py` / `tx_*.py`, any function starting with `tx_` or `transmit_`, any
  module named `transmitter` / `jammer` / `injector`, and any subprocess
  invocation of a TX binary (`uhd_siggen`, `hackrf_transfer -t`, etc.). The
  name/pattern is the trigger — renaming it to escape the gate doesn't bypass
  the rule.
- **Enabling the TX requires explicit human sign-off in that specific
  change** — never an agent's own initiative, never justified by a downstream
  goal. If you're writing code and the "obvious" next step is to make the TX
  reachable, *stop* — that's the line, and it's the one that needs a human.
- **The gate is load-bearing.** Removing it, or routing around it (a new
  function that does what `tx_stream` does, a subprocess call to a TX script,
  a "temporary" flag defaulting to on), is a security regression, equivalent
  to deleting an RLS policy to "unblock a feature."

### 4. Bench/owned-only is a mandatory constraint, not a suggestion

All real-RF and serial interaction is on **owned hardware, on the bench**
(AGENTS.md §0/§2). An agent generating code must not introduce a path that
targets anything other than the owned device:

- **No code that targets a live, in-the-air, or third-party link.** A
  function that takes an arbitrary frequency / UID / target and transmits is
  out of scope even if it "only works on the bench today" — the *capability*
  is the problem, not the current use.
- **Serial work: no state-modifying commands until the parser is understood.**
  "State-modifying" means **changes to the device's persistent state** — flash
  writes, config writes that take effect across reboots, bind-UID changes,
  firmware updates. These stay out of generated code until a human approves
  that specific change. **Query-style writes are fine** — a `write()` syscall
  that sends a read-request frame (e.g. `tools/e8_read.py` sends the 64-byte
  Read command to receive the 17-byte config response) does NOT change device
  state and is in-scope under the read-only rule. The distinction is "writes
  that modify the device's stored state" (gated) vs. "writes that are the
  request half of a query/response" (allowed).
- **The enclave has no SDR and no device.** andeds (enclave) writes code +
  does *offline* analysis of uploaded captures. Code that assumes a live
  radio or a connected device is a laptop-side concern; the enclave code must
  degrade gracefully (skip the live path) when no device is present.

### 5. Input validation at every boundary

Any value that crosses a boundary (CLI arg, capture `.meta.txt`, serial
frame field, UI form, config file) is validated at the boundary:

- **CLI / config inputs:** type-check + range-check. A sample-rate of `abc`
  or a channel count of `-5` is rejected, not coerced into a crash.
- **Serial frame fields:** every field read from a 64-byte (or 17-byte) frame
  is bounds-checked against the actual buffer length *and* sanity-checked
  against the expected range (a "rate" byte of `0xFF` when only `0x00-0x03`
  are valid is a malformed frame, not a rate).
- **No permissive defaults.** A missing / invalid value falls back to a
  *safe, explicit* default (and ideally logs the fallback) — not to a
  permissive "accept anything" that downstream code then trusts.

### 6. Function / module hardening

- **Small, single-purpose parsers.** Each protocol field / frame type gets
  its own function that validates its own input and returns a typed result
  (a dataclass) or an error. No monolithic "parse everything" function that
  shares mutable state across field types — that's where a bad field in one
  frame corrupts the next.
- **Pure parse functions where possible.** The decode / classify functions
  (`elrs_sdr/core/*`) should be pure (input bytes → output result, no global
  state, no I/O) so they're unit-testable and can't have side effects on a
  malformed input. I/O (reading the capture file, opening the serial port)
  stays at the edges.
- **Restricted surface.** A module should expose only what callers need. The
  TX side of `radio.py` is not part of the public surface the detector / UI
  imports (see rule 3). Don't add a convenient re-export that makes the gated
  path one import away.

---

## Pre-merge checklist

Rules only work if they're verified. Run this before a change lands (the
laptop runs the real pytest; the enclave verifies logic by simulation —
AGENTS.md §4):

- [ ] **Secret scan** — `git diff` the change; no UID-as-constant, no key /
      token / passphrase / binding phrase in the new code or fixtures. Grep
      for the UID in **every form an agent might generate it**, not just the
      hex pair-string. From a gitignored `secrets.local` that holds the real
      UID bytes, a check script would scan the staged diff for:
      - hex pair strings (`5d05...`, `5D 05 ...`, `0x5d, 0x05, ...`)
      - the decimal tuple form (`93, 5, 0, 17, 24, 91`)
      - `bytes([...])` / `b"\x5d\x05\x00\x11\x18\x5b"` constructions
      - any `UID = ` / `BIND = ` / `*_bytes = ` assignment with those values
      - the same patterns in `.ts` / `.json` / fixtures, not just `.py`
      The `rg` grep in the earlier version catches `=`-style assignments only
      and misses the common tuple / bytes-list forms — treat it as a floor,
      not a ceiling, until a pre-commit hook is wired up to the local secret
      list.
- [ ] **Bounds-check the parsers** — any new / changed code that indexes into
      a capture or serial frame validates the length *before* the index.
- [ ] **TX gate intact (current phase)** — a passive change does not import /
      call / wire in `tx_tune` / `tx_stream` from a detector / analysis / UI /
      sweep path; the passive tool stays receive-only. The CLI `tx` command is
      a deliberate gated capability (see rule 3) — touching that TX path, or
      enabling any transmit / flash / persistent device change, has explicit
      human sign-off in the commit message. (QC-001: the current flag-only gate
      is a known weak point to be replaced with a proper sign-off mechanism in
      the TX phase.)
- [ ] **No live / third-party target** — the change does not add a path that
      targets anything other than the owned bench device.
- [ ] **Serial work is read-only** — no flash / write / update / device-
      modifying command is introduced in the native serial tooling (until the
      parser is understood + a human approves a specific write).
- [ ] **Input validation at boundaries** — every new CLI arg / config value /
      frame field is type- + range-checked.
- [ ] **Fail closed** — a malformed / short / bad-CRC input returns an error
      result, not a partially-filled struct.
- [ ] **Selftest still passes** — `python3 -m elrs_sdr.selftest` (and the
      relevant module selftest, e.g. `python3 -m elrs_sdr.detect selftest`)
      is green.
- [ ] **No `__pycache__` in git** — `find . -name __pycache__ -type d -exec
      rm -rf {} +` before committing.
- [ ] **No debug / scratch artifacts committed** — crash dumps
      (`backtrace.txt`, `core.*`), wine logs (`wine*.log`, `*wine*trace*`),
      strace outputs (`*strace*.log`), memory dumps (`unpacked.bin`,
      `/tmp/*.dump`), and ad-hoc scratch files from an analysis session stay
      out of the tree. These leak environment detail and bloat history for no
      downstream benefit. If one needs to survive the session, store it under
      a gitignored path or attach it to a note.

---

## Enforced architecture

**Receive-only by construction (current phase).** The detector, the analysis,
and the WaveRaider UI are all *read* paths, and in the current phase they are
**not reachable from** the gated TX side — the passive detector / analysis /
UI flows do not call `tx_tune`/`tx_stream`. That is the backbone of the threat
model: an agent can't accidentally turn the *passive* tool into a transmitter
by generating a "convenient" call, because the TX surface isn't importable
from the detector/UI flows. **Honest current-state note:** the CLI *does*
expose a `tx` subcommand (`elrs_sdr/cli.py:737-755`) that reaches
`tx_tune`/`tx_stream`, gated only by a self-asserted
`--i-am-authorized-to-transmit` flag. That is a **deliberate gated
capability, not a passive-path leak** — but the flag-only gate is a known weak
point (**QC-001**) to be replaced with a proper human sign-off mechanism when
the TX phase starts. If a *passive* change (detector/analysis/UI) would make
the TX path reachable, that's the security regression to flag.

**Untrusted input stays untrusted.** Captures and serial frames are parsed by
pure, bounds-checked functions that return typed results or errors. The
parsed values are *data* — they are never executed, never used as indices
without a bounds check, and never trusted to be complete. A frame that's
malformed is *rejected*, not *coerced*.

**Own-device by default.** Every code path that touches a radio or a serial
port targets the owned bench device. There is no code path that targets an
arbitrary / live / third-party link. The "target" is the operator's own
hardware, and that's a constraint on the *capability*, not just the current
configuration.

---

## Why this works (the principle)

AI agents are context-driven. They read the repo, follow the patterns, and
generate code that matches the style of what's already there. This file makes
the security posture *explicit and parseable* so the agent generates the
secure pattern by default:

- When an agent sees "no secrets in code" + "the UID is a pairing secret,
  never a constant," it takes the UID as a runtime input instead of
  inlining it.
- When an agent sees "the TX gate is a security boundary," it doesn't add a
  "convenient" `tx_stream` call to make a feature work — it stops and asks.
- When an agent sees "validate before you parse" + "fail closed," it writes
  the bounds check + the error return instead of the bare index that crashes
  on a short frame.

This is the source of truth for "how we handle security here" — whether the
code is written by a person or a model. It's the cheapest, highest-leverage
safety net in the repo: it catches the most common agent mistakes *at the
point of generation*, before they reach a review.

**When in doubt:** no secrets, validate the input, keep the TX gate closed *for
the current phase* (staged control — bench-only + human sign-off, not a wall
around an out-of-scope feature), stay on the owned bench device, stay
read-only until a human approves a write, and fail closed. If a change needs to
step outside any of those, it needs explicit human sign-off in that specific
change — not an agent's own initiative.
