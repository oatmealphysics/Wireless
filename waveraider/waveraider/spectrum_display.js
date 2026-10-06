/* Presentation pacing only: never changes radio capture or audio scheduling. */
class SpectrumDisplay {
  constructor() {
    this.pace = 'steady';
    this.paused = false;
    this.reset();
  }
  settings() {
    return {live: [0, 0, 0, 0], steady: [1000, 5000, 2000, 5000],
            slow: [2000, 10000, 3500, 8000]}[this.pace];
  }
  reset() {
    this.key = null; this.latest = null; this.frame = null;
    this.smooth = []; this.hold = []; this.until = [];
    this.lastSample = null; this.lastPlot = -Infinity; this.lastList = -Infinity;
    this.paused = false;
  }
  setPace(pace) {
    if (!['live', 'steady', 'slow'].includes(pace)) return;
    this.pace = pace;
    this.lastPlot = this.lastList = -Infinity;
  }
  accept(d, now) {
    const key = [d.device, d.source, d.start_hz, d.stop_hz, d.points.length].join('|');
    if (key !== this.key) { this.reset(); this.key = key; }
    const [, , tau, holdMs] = this.settings();
    const dt = this.lastSample === null ? 0 : Math.max(0, now - this.lastSample);
    const alpha = !tau || this.lastSample === null ? 1 : 1 - Math.exp(-dt / tau);
    d.points.forEach((p, i) => {
      this.smooth[i] = this.lastSample === null ? p[1] : this.smooth[i] + alpha * (p[1] - this.smooth[i]);
      if (this.lastSample === null || p[1] >= this.hold[i]) {
        this.hold[i] = p[1]; this.until[i] = now + holdMs;
      } else {
        // Hold transients, then decay at 6 dB/sec instead of dropping instantly.
        const decayMs = Math.max(0, now - Math.max(this.lastSample, this.until[i]));
        this.hold[i] = Math.max(p[1], this.hold[i] - decayMs * .006);
      }
    });
    this.latest = d; this.lastSample = now;
  }
  present(now, interacting = false, force = false) {
    if (!this.latest || this.paused) return {};
    const [plotMs, listMs] = this.settings(), result = {};
    if (force || now - this.lastPlot >= plotMs) {
      const d = this.latest;
      const points = d.points.map((p, i) => [p[0], this.pace === 'live' ? p[1] : this.smooth[i]]);
      const peak = points.reduce((a, b) => b[1] > a[1] ? b : a, points[0]);
      this.frame = {...d, points, peak: {freq_hz: peak[0], db: Math.round(peak[1] * 10) / 10},
                    display_hold: this.pace === 'live' ? null : this.hold.slice()};
      result.plot = this.frame;
      this.lastPlot = now;
    }
    if ((!interacting || this.lastList === -Infinity) && (force || now - this.lastList >= listMs)) {
      // Frequency order is stable when strength rankings swap between sweeps.
      result.list = {...this.latest, signals: [...(this.latest.signals || [])].sort((a,b) => a.freq_hz - b.freq_hz)};
      this.lastList = now;
    }
    return result;
  }
}
