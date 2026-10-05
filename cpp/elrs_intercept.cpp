// elrs_intercept.cpp — Passive ELRS interceptor for a B205mini (UHD).
//
// Implements the PROVEN 4-phase decode model from LoRa-ELRS-Interceptor-Decoder,
// adapted from an ESP32+LR1121 radio to a software UHD capture + demod:
//
//   HUNT    sit on a fixed channel, wait for the FHSS sequence to cross it,
//           catch a SYNC packet, extract 3 of 4 UID bytes.
//   EXPLOIT 256-guess the hidden UID byte; for each, build the Fisher-Yates
//           hop table, tune to the predicted next hop, listen one hop interval.
//           A CRC-valid RC packet locks the seed.
//   LOCK    track the hop sequence in real time, dead-reckon on misses, fall
//           back to HUNT after too many misses.
//   DECODE  CRC-check, dewhiten (LFSR), unpack RC channels, emit JSONL.
//
// The pure decode logic lives in elrs_decode.h (UHD-independent) and is
// unit-tested by tests/test_elrs_decode.cpp. This file is only the UHD capture
// loop + state machine, so the decode can be validated without a radio.
//
// BUILD (on Borealis, where UHD lives):  see build.sh
//   g++ -O2 -std=c++17 elrs_intercept.cpp $(pkg-config --cflags --libs libuhd) -lm

#include "elrs_decode.h"
#include "lora_demod_cpp.h"

#include <uhd/types/tune_request.h>
#include <uhd/usrp/multi_usrp.hpp>

#include <atomic>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

// ---------------------------------------------------------------------------
// JSON emitter
// ---------------------------------------------------------------------------
static std::string hexstr(const uint8_t *b, int n) {
    static const char *h = "0123456789abcdef";
    std::string s; s.reserve(n * 2);
    for (int i = 0; i < n; i++) { s += h[b[i] >> 4]; s += h[b[i] & 0xf]; }
    return s;
}
static double now_s() {
    return std::chrono::duration<double>(
               std::chrono::system_clock::now().time_since_epoch()).count();
}
static void emit(std::ofstream &out, const char *event, uint32_t freq_hz,
                 const elrs_uid *uid, const uint8_t *pkt, const rc_frame *frame) {
    out << "{\"t\":" << now_s() << ", \"event\":\"" << event
        << "\", \"freq_hz\":" << freq_hz
        << ", \"uid\":\"" << (uid ? hexstr(uid->bytes, 4) : "????")
        << "\", \"pkt\":\"" << (pkt ? hexstr(pkt, ELRS_PACKET_SIZE) : "") << "\"";
    if (frame)
        out << ", \"armed\":" << (frame->armed ? 1 : 0)
            << ", \"active\":" << (frame->active ? 1 : 0)
            << ", \"throttle\":" << frame->channels[CH_THROTTLE]
            << ", \"roll\":" << frame->channels[CH_ROLL]
            << ", \"pitch\":" << frame->channels[CH_PITCH]
            << ", \"yaw\":" << frame->channels[CH_YAW];
    out << "}\n";
    out.flush();
}

// ---------------------------------------------------------------------------
// [RF] UHD capture — isolated from the decode logic.
// The software LoRa demod (lora_demod_cpp.h) is a faithful port of the proven
// numpy reference (tools/lora_ref.py) and is CPU-verified to recover payload
// symbols from synthetic IQ. It is NOT yet validated against real RF; the
// capture loop below runs it on both IQ polarities and feeds CRC-valid packets
// into the decode state machine. Raw IQ is also dumped for offline analysis.
// ---------------------------------------------------------------------------
struct UdhCapture {
    uhd::usrp::multi_usrp::sptr usrp;
    uhd::rx_streamer::sptr rx_stream;
    uhd::stream_args_t stream_args;        // assign-init to avoid most-vexing-parse
    uhd::rx_metadata_t meta;
    std::vector<std::complex<float>> buf;
    double srate_hz = 0.0;

    bool init(const std::string &dev, double freq_hz, double srate, double gain) {
        // ~30 ms window @ the sample rate: enough for 2-3 full SF7 packets
        // (preamble+payload ~7 ms each), small enough that the brute-force
        // demod stays fast so the HUNT sweep can step channels in ms, not s.
        buf.resize((size_t)(srate * 0.030));
        srate_hz = srate;                  // track the rate we asked for
        stream_args = uhd::stream_args_t("fc32");
        usrp = uhd::usrp::multi_usrp::make(dev);
        usrp->set_rx_freq(freq_hz, 0);
        usrp->set_rx_rate(srate, 0);
        usrp->set_rx_gain(gain, 0);
        // Common B200 all-zero/DC-clipped fixes: make sure the RX chain is
        // actually armed on a real antenna (default can be a DC path on some
        // firmware), and enable the DC-offset / IQ-imbalance compensation.
        try { usrp->set_rx_antenna("RX2", 0); } catch (const std::exception &e) {
            std::fprintf(stderr, "[init] set_rx_antenna(RX2) failed: %s\n", e.what());
        }
        try { usrp->set_dc_offset_mode(true, 0); } catch (const std::exception &e) {
            std::fprintf(stderr, "[init] set_dc_offset_mode failed: %s\n", e.what());
        }
        try { usrp->set_iq_balance_mode(true, 0); } catch (const std::exception &e) {
            std::fprintf(stderr, "[init] set_iq_balance_mode failed: %s\n", e.what());
        }
        rx_stream = usrp->get_rx_stream(stream_args);
        // (No stream_info_t / get_stream_info() — not present in this UHD build.
        //  We only need the rate, which we already have from set_rx_rate.)
        return true;
    }
    void set_buf(size_t n) { buf.resize(n); }
    size_t read(std::vector<std::complex<float>> &out) {
        // recv(buffs, nsamps, metadata&, timeout) -> size_t (num samps)
        size_t n = rx_stream->recv(&out[0], out.size(), meta, 0.5);
        // One-shot startup diagnostic: if the stream is delivering all-zeros or
        // zero samples, the whole decode is doomed before DSP. Print the first
        // recv's count + a sample histogram (exactly-zero vs nonzero + max mag).
        static bool diag_done = false;
        if (!diag_done) {
            diag_done = true;
            std::fprintf(stderr, "[diag] first recv: got=%zu of %zu\n", n, out.size());
            int zeros = 0, nz = 0; double maxm = 0.0;
            size_t lim = std::min<size_t>(n, 1000);
            for (size_t i = 0; i < lim; i++) {
                double m = std::abs(out[i]);
                if (m == 0.0) zeros++; else { nz++; if (m > maxm) maxm = m; }
            }
            std::fprintf(stderr, "[diag] first %zu: zeros=%d nonzero=%d maxmag=%.6e\n",
                         lim, zeros, nz, maxm);
        }
        return n;
    }
    void tune(double freq_hz) { usrp->set_rx_freq(freq_hz, 0); }
    double srate() const { return srate_hz; }
};

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------
enum phase { HUNT = 0, EXPLOIT, LOCK };

int main(int argc, char **argv) {
    // Modes:
    //   elrs_intercept [dev] [hunt_MHz] [srate] [gain] [out.jsonl]   (ELRS decode)
    //   elrs_intercept --scan [dev] [gain] [dwell_ms]                (2.4 GHz power scan)
    bool scan_mode = false;
    int  a = 1;
    if (argc > 1 && std::string(argv[1]) == "--scan") { scan_mode = true; a = 2; }

    std::string dev;
    double hunt_mhz, srate, gain;
    std::string outp;
    int dwell_ms;
    if (scan_mode) {
        dev     = argc > a     ? argv[a]     : "type=b200";
        gain    = argc > a + 1 ? atof(argv[a+1]) : 40.0;
        dwell_ms= argc > a + 2 ? atoi(argv[a+2]) : 200;   // ms per channel
        hunt_mhz= 2400.4; srate = 10.0e6; outp = "scan.jsonl";
    } else {
        dev    = argc > 1 ? argv[1] : "type=b200";
        hunt_mhz= argc > 2 ? atof(argv[2]) : 2430.0;
        srate  = argc > 3 ? atof(argv[3]) : 10.0e6;
        gain   = argc > 4 ? atof(argv[4]) : 40.0;
        outp   = argc > 5 ? argv[5] : "elrs_capture.jsonl";
        dwell_ms = 200;
    }

    std::ofstream out(outp.c_str());
    std::fprintf(stderr, "[init] dev=%s hunt=%.3f MHz srate=%.1f MS/s gain=%.1f\n",
                 dev.c_str(), hunt_mhz, srate / 1e6, gain);
    std::fprintf(stderr, "[init] model: SF=%d BW=%d kHz N=%lu (ISM2G4 2400.4-2479.4 MHz) "
                 "sync_ch=%lu (2440 MHz) CRC=14bit poly=0x%04X\n",
                 ELRS_SF, ELRS_BW_KHZ, (unsigned long)ELRS_N_FREQ,
                 (unsigned long)ELRS_SYNC_CHANNEL, ELRS_CRC14_POLY);
    std::fprintf(stderr, "[init] demod=software (both IQ polarities, byte-phase = real-RF)\n");

    UdhCapture cap;
    if (!cap.init(dev, hunt_mhz * 1e6, srate, gain)) {
        std::fprintf(stderr, "[init] UHD init failed\n");
        return 1;
    }

    // ------------------------------------------------------------------
    // SCAN MODE: sweep ISM2G4, measure per-channel power, find the signal.
    // No demod. Reports where the 2.4 GHz energy is (fixed vs hopping),
    // its magnitude, and confirms the RF path is alive.
    // ------------------------------------------------------------------
    if (scan_mode) {
        // Power averaging wants a bigger buffer than the 30 ms demod window.
        // Use ~100 ms per channel for a stable mean.
        size_t scan_win = (size_t)(srate * 0.100);
        cap.set_buf(scan_win);
        std::fprintf(stderr, "[scan] sweeping ISM2G4: %lu ch, %.0f ms dwell/ch, gain=%.1f\n",
                     (unsigned long)ELRS_N_FREQ, dwell_ms, gain);
        std::fprintf(stderr, "[scan]  ch  freq_MHz   mean_pk  mean  (pk/mean ratio)\n");

        double all_mean[ELRS_N_FREQ];
        double all_pk[ELRS_N_FREQ];
        auto tscan = std::chrono::steady_clock::now();
        for (size_t ch = 0; ch < ELRS_N_FREQ; ch++) {
            double mhz = freq_for_index((uint8_t)ch) / 1e6;
            cap.tune((uint32_t)(mhz * 1e6));
            // Settle briefly after retune (PLL/filters), then average power.
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            std::vector<std::complex<float>> win(scan_win);
            size_t got = 0; double sum = 0.0; double peak = 0.0;
            auto td = std::chrono::steady_clock::now();
            while (std::chrono::duration<double, std::milli>(
                       std::chrono::steady_clock::now() - td).count() < dwell_ms) {
                size_t n = cap.read(win);
                for (size_t i = 0; i < n; i++) {
                    double m = std::abs(win[i]);
                    sum += m * m;              // mean *power* (|x|^2), not magnitude
                    if (m > peak) peak = m;
                }
                got += n;
            }
            double mean_pow = got ? (sum / (double)got) : 0.0;
            double mean_mag = got ? std::sqrt(mean_pow) : 0.0;   // RMS magnitude
            all_mean[ch] = mean_mag; all_pk[ch] = peak;
            // One-shot diag on the first channel so we know the RF is alive.
            if (ch == 0) {
                std::fprintf(stderr, "[scan]   ch0 first-recv got=%zu (RF alive check)\n", got);
            }
            std::fprintf(stderr, "[scan]  %2zu  %8.3f   %8.5f  %8.5f  (%s)\n",
                         ch, mhz, peak, mean_mag,
                         (peak > 0 && mean_mag > 0) ?
                            (peak / mean_mag > 3.0 ? "SPICY" : "flat") : "-");
        }
        // Summary: rank channels by mean power, flag the loud ones.
        std::fprintf(stderr, "\n[scan] top 10 channels by mean power:\n");
        std::vector<size_t> order(ELRS_N_FREQ);
        for (size_t i = 0; i < ELRS_N_FREQ; i++) order[i] = i;
        std::sort(order.begin(), order.end(),
                  [&](size_t a, size_t b){ return all_mean[a] > all_mean[b]; });
        double floormax = 0.0;   // the noise floor = max of the quietest half
        for (size_t i = ELRS_N_FREQ / 2; i < ELRS_N_FREQ; i++)
            if (all_mean[order[i]] > floormax) floormax = all_mean[order[i]];
        for (size_t i = 0; i < 10 && i < ELRS_N_FREQ; i++) {
            size_t c = order[i];
            double db = (floormax > 0 && all_mean[c] > 0)
                          ? 20.0 * std::log10(all_mean[c] / floormax) : 0.0;
            std::fprintf(stderr, "[scan]   #%2zu ch=%2zu freq=%.3f MHz mean=%.5f peak=%.5f (+%.1f dB over floor)\n",
                         i + 1, c, freq_for_index((uint8_t)c) / 1e6,
                         all_mean[c], all_pk[c], db);
        }
        double scansec = std::chrono::duration<double>(
                             std::chrono::steady_clock::now() - tscan).count();
        std::fprintf(stderr, "[scan] done in %.1fs. Floor~%.5f. If a real RC link is on, expect 1+ channels +5-20 dB over floor.\n",
                     scansec, floormax);
        // Emit a JSONL summary for later analysis.
        std::ofstream sjson("scan.jsonl");
        if (sjson) {
            for (size_t i = 0; i < ELRS_N_FREQ; i++)
                sjson << "{\"ch\":" << i << ",\"freq\":" << (freq_for_index((uint8_t)i)/1e6)
                      << ",\"mean\":" << all_mean[i] << ",\"peak\":" << all_pk[i] << "}\n";
        }
        return 0;
    }

    // Dump raw IQ for offline demod validation.
    std::string iqp = outp + ".iq";
    FILE *iqf = fopen(iqp.c_str(), "wb");

    auto t0 = std::chrono::steady_clock::now();
    phase st = HUNT;
    elrs_uid uid{}; uid.complete = false;
    uint32_t hop_table[ELRS_N_FREQ];
    uint8_t  hop_idx = 0;
    int      missed  = 0;
    size_t   nwin = 0;

    // HUNT/EXPLOIT sweep: the drone hops 80 channels ~150x/s, so we sweep the
    // ISM2G4 band rather than camp on one freq. Each capture window is ~30 ms;
    // dwell_windows = how many to sit on each channel before stepping. With a
    // fast demod, 1 window/ch steps the band every ~130 ms -> full sweep ~10 s.
    const size_t   dwell_windows = 1;
    size_t         sweep_ch = 0;          // channel index within the sweep
    size_t         sweep_dwell = 0;       // windows spent on current channel
    double         sweep_mhz = hunt_mhz;  // current tuned freq during sweep
    cap.tune((uint32_t)(sweep_mhz * 1e6));

    std::fprintf(stderr, "[run] capturing (Ctrl-C to stop)...\n");
    std::fprintf(stderr, "[run] HUNT sweep: 80 ch 2400.4-2479.4 MHz, dwell=%zu wins/ch\n",
                 dwell_windows);
    double cur_mhz = sweep_mhz;   // currently-tuned freq (updated on hop/sweep)
    while (true) {
        std::vector<std::complex<float>> win(cap.buf.size());
        size_t got = cap.read(win);
        if (iqf) fwrite(win.data(), sizeof(std::complex<float>), got, iqf);

        double elapsed = std::chrono::duration<double>(
                             std::chrono::steady_clock::now() - t0).count();
        nwin++;
        // Peak + mean magnitude over the window: a cheap "is there signal here"
        // readout that works before we decode anything. Lets us see the drone's
        // energy across the sweep and confirm gain is sane (noise floor vs signal).
        double peak = 0.0, sum = 0.0;
        for (size_t i = 0; i < got; i++) {
            double m = std::abs(win[i]);
            if (m > peak) peak = m;
            sum += m;
        }
        double mean = got ? (sum / (double)got) : 0.0;
        // Log every channel step in HUNT (so the sweep walk is visible) and
        // periodically in LOCK.
        bool log = (st == HUNT) || (st == EXPLOIT) || (nwin % 50 == 1);
        if (log)
            std::fprintf(stderr,
                "[run] t=%.1fs wins=%zu phase=%d freq=%.3f MHz pk=%.4f mean=%.4f\n",
                elapsed, nwin, st, cur_mhz, peak, mean);

        // Software demod (both IQ polarities — ELRS InvertIQ). demod_iq returns
        // a stream of payload bytes; we slide an 8-byte window over it.
        //
        // Detection model (upstream ELRS OTA4):
        //   HUNT   : find a SYNC packet — type=PACKET_TYPE_SYNC (byte0 low bits)
        //            + CRC-14 valid with nonceValidator=0. SYNC carries UID4,
        //            UID5 in clear; build the hop table from those.
        //   EXPLOIT: find a CRC-valid RC packet (type=RCDATA) by brute-forcing
        //            the 8-bit nonce (a passive interceptor doesn't track
        //            OtaNonce). Lock onto the hop sequence.
        //   LOCK   : follow the hop sequence, decode RC frames.
        for (bool inv : {false, true}) {
            std::vector<uint8_t> bytes =
                lora::demod_iq(win.data(), got, ELRS_SF, ELRS_BW_KHZ * 1000,
                               cap.srate(), inv);
            for (size_t off = 0; off + ELRS_PACKET_SIZE <= bytes.size(); off++) {
                const uint8_t *pkt = &bytes[off];
                uint8_t ptype = packet_type(pkt);

                if (st == HUNT && ptype == PKT_TYPE_SYNC) {
                    elrs_uid u; u.complete = true;
                    if (extract_uid(pkt, u.bytes)) {
                        // Confirm the SYNC is CRC-valid (nonceValidator=0).
                        uint8_t nonce;
                        if (validate_ota4(pkt, u.bytes, true, &nonce)) {
                            uid = u; st = EXPLOIT;
                            generate_hop_table(uid.bytes, hop_table);
                            // The SYNC packet's byte[0] = fhssIndex: the exact
                            // channel the drone is hopping to/around right now.
                            // Use it as the precise starting hop (fall back to
                            // the sweep channel if it's out of range).
                            uint8_t fhss_idx = pkt[0];
                            hop_idx = (fhss_idx < ELRS_N_FREQ) ? fhss_idx : (uint8_t)sweep_ch;
                            std::fprintf(stderr,
                                "[hunt] SYNC on %.3f MHz (fhssIdx=%u), UID4=%02X UID5=%02X -> EXPLOIT\n",
                                sweep_mhz, fhss_idx, u.bytes[2], u.bytes[3]);
                            emit(out, "sync_hunt", (uint32_t)(sweep_mhz*1e6), &uid, pkt, nullptr);
                        }
                    }
                } else if (st == EXPLOIT && ptype == PKT_TYPE_RCDATA) {
                    uint8_t nonce;
                    if (validate_ota4(pkt, uid.bytes, false, &nonce)) {
                        st = LOCK;
                        // Start dead-reckoning from the channel we're on now.
                        hop_idx = (uint8_t)sweep_ch;
                        cur_mhz = hop_table[hop_idx] / 1e6;
                        cap.tune(hop_table[hop_idx]);
                        std::fprintf(stderr,
                            "[exploit] RC CRC-valid (nonce=%u) -> LOCK, hop_idx=%u freq=%.6f MHz\n",
                            nonce, hop_idx, cur_mhz);
                        emit(out, "lock", (uint32_t)hop_table[hop_idx], &uid, pkt, nullptr);
                    }
                } else if (st == LOCK && ptype == PKT_TYPE_RCDATA) {
                    uint8_t nonce;
                    if (!validate_ota4(pkt, uid.bytes, false, &nonce)) continue;
                    rc_frame f = decode_rc_packet(pkt, uid.bytes);
                    missed = 0;
                    emit(out, "rc", (uint32_t)hop_table[hop_idx], &uid, pkt, &f);
                }
            }
        }

        // HUNT/EXPLOIT: sweep the band. Step to the next channel after the
        // dwell; LOCK dead-reckons instead (handled below).
        if (st == HUNT || st == EXPLOIT) {
            sweep_dwell++;
            if (sweep_dwell >= dwell_windows) {
                sweep_dwell = 0;
                sweep_ch = (sweep_ch + 1) % ELRS_N_FREQ;
                sweep_mhz = freq_for_index((uint8_t)sweep_ch) / 1e6;
                cur_mhz = sweep_mhz;
                cap.tune((uint32_t)(sweep_mhz * 1e6));
            }
        }

        // Dead-reckon hop advancement in LOCK (timing-driven).
        if (st == LOCK) {
            // Advance one hop per hop_interval, retuning each time. The proven
            // reference (phase3_lockons) anchors hop timing to the SYNC capture
            // with µs precision; this is a coarser wall-clock version — good
            // enough to follow the sequence, not bit-locked.
            static auto last_hop = std::chrono::steady_clock::now();
            auto now = std::chrono::steady_clock::now();
            double dt_us = std::chrono::duration<double, std::micro>(now - last_hop).count();
            if (dt_us >= (double)ELRS_HOP_US) {
                hop_idx = (hop_idx + 1) % ELRS_N_FREQ;
                cur_mhz = hop_table[hop_idx] / 1e6;
                cap.tune(hop_table[hop_idx]);
                last_hop = now;
            }
        }
    }
    if (iqf) fclose(iqf);
    return 0;
}
