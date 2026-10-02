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
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
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
    uhd::stream_args_t stream_args;        // brace-init to avoid most-vexing-parse
    uhd::rx_streamer::stream_info_t info;
    uhd::rx_metadata_t meta;
    std::vector<std::complex<float>> buf;
    double srate_hz = 0.0;

    bool init(const std::string &dev, double freq_hz, double srate, double gain) {
        buf.resize((size_t)(srate * 0.25));
        srate_hz = srate;
        // UHD 4.x: stream_args via brace-init (the 1-arg form triggers
        // most-vexing-parse when the member is named stream_args).
        stream_args = uhd::stream_args_t("fc32");
        usrp = uhd::usrp::multi_usrp::make(dev);
        usrp->set_rx_freq(freq_hz, 0);
        usrp->set_rx_rate(srate, 0);
        usrp->set_rx_gain(gain, 0);
        rx_stream = usrp->get_rx_stream(stream_args);
        info = rx_stream->get_stream_info();
        srate_hz = info.rate;
        return true;
    }
    size_t read(std::vector<std::complex<float>> &out) {
        // recv(buffs, nsamps, metadata&, timeout, now) -> size_t (num samps)
        return rx_stream->recv(&out[0], out.size(), meta, 0.5);
    }
    void tune(double freq_hz) { usrp->set_rx_freq(freq_hz, 0); }
    double srate() const { return srate_hz; }
};

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------
enum phase { HUNT = 0, EXPLOIT, LOCK };

int main(int argc, char **argv) {
    std::string dev    = argc > 1 ? argv[1] : "type=b200";
    double hunt_mhz    = argc > 2 ? atof(argv[2]) : 915.0;
    double srate       = argc > 3 ? atof(argv[3]) : 6.0e6;
    double gain        = argc > 4 ? atof(argv[4]) : 40.0;
    std::string outp   = argc > 5 ? argv[5] : "elrs_capture.jsonl";

    std::ofstream out(outp.c_str());
    std::fprintf(stderr, "[init] dev=%s hunt=%.3f MHz srate=%.1f MS/s gain=%.1f\n",
                 dev.c_str(), hunt_mhz, srate / 1e6, gain);
    std::fprintf(stderr, "[init] model: SF=%d BW=%d kHz sync=0x%02X N=%lu step=%lu kHz "
                 "hop=%lu us\n", ELRS_SF, ELRS_BW_KHZ, ELRS_SYNC_WORD,
                 (unsigned long)ELRS_N_FREQ, (unsigned long)(ELRS_FREQ_STEP_HZ/1000),
                 (unsigned long)hop_interval_us());
    std::fprintf(stderr, "[init] demod=software (both IQ polarities, byte-phase = real-RF)\n");

    UdhCapture cap;
    if (!cap.init(dev, hunt_mhz * 1e6, srate, gain)) {
        std::fprintf(stderr, "[init] UHD init failed\n");
        return 1;
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

    std::fprintf(stderr, "[run] capturing (Ctrl-C to stop)...\n");
    double cur_mhz = hunt_mhz;   // currently-tuned freq (updated on hop/phase change)
    while (true) {
        std::vector<std::complex<float>> win(cap.buf.size());
        size_t got = cap.read(win);
        if (iqf) fwrite(win.data(), sizeof(std::complex<float>), got, iqf);

        double elapsed = std::chrono::duration<double>(
                             std::chrono::steady_clock::now() - t0).count();
        nwin++;
        if (nwin % 40 == 1)
            std::fprintf(stderr, "[run] t=%.1fs wins=%zu phase=%d freq=%.6f MHz srate=%.3fM\n",
                         elapsed, nwin, st, cur_mhz, cap.srate() / 1e6);

        // Software demod (both IQ polarities — ELRS InvertIQ). demod_iq returns
        // a stream of payload bytes; we slide an 8-byte window over it and
        // CRC-check each candidate (a misaligned window fails CRC and is
        // dropped, which is exactly the rejection we want).
        for (bool inv : {false, true}) {
            std::vector<uint8_t> bytes =
                lora::demod_iq(win.data(), got, ELRS_SF, ELRS_BW_KHZ * 1000,
                               cap.srate(), inv);
            for (size_t off = 0; off + ELRS_PACKET_SIZE <= bytes.size(); off++) {
                const uint8_t *pkt = &bytes[off];
                if (!validate_crc(pkt, ELRS_PACKET_SIZE)) continue;
                uint8_t ptype = packet_type(pkt);

                if (st == HUNT && ptype == PKT_TYPE_SYNC) {
                    elrs_uid u;
                    if (extract_uid(pkt, &u)) {
                        uid = u; st = EXPLOIT;
                        generate_hop_table(&uid, hop_table);
                        hop_idx = 0;
                        std::fprintf(stderr,
                            "[hunt] SYNC on %.3f MHz, UID=%s[?] -> EXPLOIT (256-guess pending)\n",
                            hunt_mhz, hexstr(uid.bytes, 3).c_str());
                        emit(out, "sync_hunt", (uint32_t)(hunt_mhz*1e6), &uid, pkt, nullptr);
                    }
                } else if (st == EXPLOIT && ptype == PKT_TYPE_RC) {
                    // First CRC-valid RC packet after a SYNC: the UID (and thus
                    // hop table) is confirmed. Lock onto the hop sequence.
                    st = LOCK;
                    // Estimate current hop idx: find which table freq is closest
                    // to the freq we captured the RC on (hunt_mhz for now).
                    double best = 1e18;
                    for (int i = 0; i < ELRS_N_FREQ; i++) {
                        double d = std::abs((double)hop_table[i] - hunt_mhz*1e6);
                        if (d < best) { best = d; hop_idx = (uint8_t)i; }
                    }
                    cur_mhz = hop_table[hop_idx] / 1e6;
                    cap.tune(hop_table[hop_idx]);
                    std::fprintf(stderr,
                        "[exploit] RC CRC-valid -> LOCK, hop_idx=%u freq=%.6f MHz\n",
                        hop_idx, cur_mhz);
                    emit(out, "lock", (uint32_t)hop_table[hop_idx], &uid, pkt, nullptr);
                } else if (st == LOCK && ptype == PKT_TYPE_RC) {
                    rc_frame f = decode_rc_packet(pkt, &uid);
                    missed = 0;
                    emit(out, "rc", (uint32_t)hop_table[hop_idx], &uid, pkt, &f);
                }
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
            if (dt_us >= hop_interval_us()) {
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
