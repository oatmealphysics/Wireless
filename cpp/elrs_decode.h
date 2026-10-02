// elrs_decode.h — UHD-independent ELRS decode logic (UPSTREAM ELRS model).
//
// Pure C++ (no UHD, no radio). Shared by elrs_intercept.cpp (the live tool)
// and tests/test_elrs_decode.cpp (the CPU-only unit test).
//
// The FHSS / PRNG / UID-seed / CRC model here is ported EXACTLY from the
// upstream ExpressLRS source (drone_refs/_ext/ExpressLRS/ExpressLRS-master):
//   - src/lib/FHSS/random.cpp      rng() LCG 214013/2531011 mod 2^31
//   - src/lib/FHSS/FHSS.cpp        FHSSrandomiseFHSSsequenceBuild (block shuffle)
//   - src/lib/FHSS/FHSS.cpp:22-56  domain tables (ISM2G4: 80ch, 2400.4-2479.4 MHz)
//   - src/lib/OTA/OTA.cpp:41       OtaGetUidSeed = UID[2..3] + (UID[4]<<8) + (UID[5]^OTA_VERSION_ID)
//   - src/src/common.cpp:31-45     2.4 GHz air rates (SF5-8 @ 800 kHz BW)
//
// NOTE: this REPLACES the earlier model that mirrored the
// LoRa-ELRS-Interceptor-Decoder reference (Fisher-Yates / LCG-2891336453 /
// 13ch @ 903.5+500kHz). That reference was a *different* ELRS variant; for
// stock ExpressLRS on 2.4 GHz the upstream algorithm below is authoritative.

#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cmath>

// =========================================================================
// LoRa PHY — 2.4 GHz ELRS (ISM2G4). Stock default is RATE_LORA_2G4_150HZ:
//   SF7, BW 800 kHz, CR 4/8 (LI). We default to SF7/800 and expose SF/BW as
//   tunables so the tool can try SF5/6/8 if the link uses another rate.
// =========================================================================
static constexpr uint8_t  ELRS_SYNC_WORD     = 0x12;   // ELRS LoRa sync word
static constexpr uint32_t ELRS_BW_KHZ        = 800;    // 2.4 GHz uses 800 kHz BW
static constexpr uint8_t  ELRS_SF            = 7;      // default; try 5/6/8 if no hit
static constexpr uint8_t  ELRS_CODING_RATE   = 8;      // CR 4/8 (LI) for 2.4 GHz
static constexpr uint8_t  ELRS_LFSR_POLY     = 0x69;   // ELRS whitening LFSR

// =========================================================================
// FHSS domain — ISM2G4 (2.4 GHz). From FHSS.cpp:
//   {"ISM2G4", 2400400000, 2479400000, 80, 2440000000}
//   (freq_start, freq_stop, freq_count, sync freq)
// FREQ_SPREAD_SCALE=256; FREQ_STEP=50000 (reg units). For the SX1280 path
// FREQ_HZ_TO_REG_VAL(freq)=freq and SPREAD_SCALE=1 in some builds, but the
// sequence index math only needs (start, stop, count) — see freq_for_index().
// =========================================================================
static constexpr uint32_t ELRS_FREQ_START_HZ = 2400400000UL;
static constexpr uint32_t ELRS_FREQ_STOP_HZ  = 2479400000UL;
static constexpr uint32_t ELRS_N_FREQ        = 80;     // 80 channels
static constexpr uint32_t ELRS_SYNC_CHANNEL  = 40;     // freq_count / 2  (2440 MHz)
static constexpr uint32_t ELRS_HOP_US        = 6666;   // 150 Hz default; 3333 for 8CH-333, 20000 for 50Hz

// =========================================================================
// Packet model
// =========================================================================
static constexpr uint8_t  ELRS_PACKET_SIZE   = 8;    // OTA4_PACKET_SIZE (v1/v2 default)
static constexpr uint8_t  ELRS_CRC_SIZE      = 2;    // CRC-16 for v2, CRC-14+2 for v1 — see crc section
static constexpr uint8_t  PKT_TYPE_RC        = 0x00;
static constexpr uint8_t  PKT_TYPE_SYNC      = 0x01;
static constexpr uint8_t  OTA_VERSION_ID     = 6;    // current OTA version (XOR'd into seed + CRC init)
static constexpr uint8_t  OTA_V1_UID_OFFSET  = 2;
static constexpr uint8_t  OTA_V2_UID_OFFSET  = 3;

// --- RC channel mapping (ELRS 16ch, 11-bit) ---
static constexpr int      CH_ROLL     = 0;
static constexpr int      CH_PITCH    = 1;
static constexpr int      CH_THROTTLE = 2;
static constexpr int      CH_YAW      = 3;
static constexpr int      CH_ARM      = 4;
static constexpr int      CH_FLIGHT   = 5;
static constexpr int      N_CHANNELS  = 16;
static constexpr uint16_t CH_CENTER   = 1024;
static constexpr uint16_t ARM_THR     = 1500;
static constexpr uint16_t DEADBAND    = 50;
static constexpr int      MAX_MISSED  = 4;

// =========================================================================
// PRNG — EXACT port of src/lib/FHSS/random.cpp
//   seed = (214013 * seed + 2531011) % 2147483648;  return seed >> 16
// =========================================================================
static inline uint16_t elrs_rng(uint32_t &seed) {
    const uint64_t m = 2147483648ULL;   // 2^31
    const uint64_t a = 214013ULL;
    const uint64_t c = 2531011ULL;
    // 64-bit multiply to avoid any 32-bit overflow ambiguity under -O2.
    seed = (uint32_t)((a * (uint64_t)seed + c) % m);
    return (uint16_t)(seed >> 16);
}
static inline uint8_t elrs_rngN(uint32_t &seed, uint8_t max) {
    return (uint8_t)(elrs_rng(seed) % max);
}

// =========================================================================
// UID -> PRNG seed — EXACT port of src/lib/OTA/OTA.cpp:41 OtaGetUidSeed()
//   (UID is a 6-byte array; the seed uses bytes [2],[3],[4],[5^OTA_VERSION_ID])
// In our elrs_uid struct (4 bytes captured from a SYNC pkt, bytes[3]=0x00
// placeholder), the mapping is: bytes[0]=UID[2], bytes[1]=UID[3],
// bytes[2]=UID[4], bytes[3]=UID[5]. So:
// =========================================================================
static inline uint32_t uid_seed(const uint8_t uid[4]) {
    return ((uint32_t)uid[0] << 24)
         | ((uint32_t)uid[1] << 16)
         | ((uint32_t)uid[2] << 8)
         |  (uint32_t)(uid[3] ^ OTA_VERSION_ID);
}

// =========================================================================
// FHSS sequence builder — EXACT port of FHSSrandomiseFHSSsequenceBuild()
//   (src/lib/FHSS/FHSS.cpp:139). Produces the hop INDEX sequence; the sync
//    channel sits at position 0 of every freqCount block, and the channel
//    that "collides" with the sync slot in a fresh block becomes 0.
// =========================================================================
static const uint16_t ELRS_SEQ_LEN = 256;   // FHSS_SEQUENCE_LEN (upstream)
static inline uint16_t build_fhss_sequence(const uint8_t uid[4], uint8_t seq[ELRS_SEQ_LEN]) {
    const uint16_t freqCount = (uint16_t)ELRS_N_FREQ;
    const uint8_t  syncChannel = (uint8_t)ELRS_SYNC_CHANNEL;
    const uint16_t sequenceCount = (ELRS_SEQ_LEN / freqCount) * freqCount;
    uint32_t seed = uid_seed(uid);

    // init
    for (uint16_t i = 0; i < sequenceCount; i++) {
        if (i % freqCount == 0)            seq[i] = syncChannel;
        else if (i % freqCount == syncChannel) seq[i] = 0;
        else                                seq[i] = (uint8_t)(i % freqCount);
    }
    // shuffle (block-local)
    for (uint16_t i = 0; i < sequenceCount; i++) {
        if (i % freqCount != 0) {
            uint8_t offset = (uint8_t)((i / freqCount) * freqCount);
            uint8_t rand   = elrs_rngN(seed, (uint8_t)(freqCount - 1)) + 1;
            uint8_t temp   = seq[i];
            seq[i]         = seq[offset + rand];
            seq[offset + rand] = temp;
        }
    }
    return sequenceCount;
}

// =========================================================================
// index -> absolute freq (Hz). ISM2G4 linear mapping over 80 channels:
//   freq(i) = start + i * (stop - start) / (count - 1)
// =========================================================================
static inline uint32_t freq_for_index(uint8_t idx) {
    uint64_t spread = (uint64_t)(ELRS_FREQ_STOP_HZ - ELRS_FREQ_START_HZ);
    uint64_t v = (spread * idx) / (ELRS_N_FREQ - 1);
    return (uint32_t)(ELRS_FREQ_START_HZ + v);
}

// Convenience: the full 80-entry freq table for a UID (sequence order).
static inline void generate_hop_table(const uint8_t uid[4], uint32_t table[ELRS_N_FREQ]) {
    uint8_t seq[ELRS_SEQ_LEN];
    uint16_t n = build_fhss_sequence(uid, seq);
    (void)n;
    for (uint32_t i = 0; i < ELRS_N_FREQ; i++)
        table[i] = freq_for_index(seq[i]);
}

struct elrs_uid { uint8_t bytes[4]; bool complete; };
struct rc_frame {
    uint16_t channels[N_CHANNELS];
    bool     armed;
    bool     active;
    uint8_t  flight_mode;
};

// =========================================================================
// CRC — ELRS OTA v2 uses CRC-16 (poly 0x1021, init from UID+version), v1
// uses CRC-14. The exact CRC is the arbiter of a real decode, so we support
// both. The 256-guess on the hidden UID byte is what makes the init correct.
// =========================================================================
static inline uint16_t crc16_ccitt(const uint8_t *data, uint8_t len, uint16_t init) {
    uint16_t crc = init;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= (uint16_t)(data[i] << 8);
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}
// CRC init for a given UID (v2): OtaCrcInitializer style =
//   ((UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID<<8))  — see OTA.cpp.
static inline uint16_t crc_init_from_uid(const uint8_t uid[4]) {
    uint8_t uid4 = uid[2], uid5 = uid[3];   // our bytes[2]=UID[4], bytes[3]=UID[5]
    return (uint16_t)(((uint16_t)uid4 << 8 | uid5) ^ ((uint16_t)OTA_VERSION_ID << 8));
}
static inline bool validate_crc_v2(const uint8_t *buf, uint8_t total_len, const uint8_t uid[4]) {
    if (total_len < 3) return false;
    uint16_t calc = crc16_ccitt(buf, total_len - 2, crc_init_from_uid(uid));
    uint16_t got  = (uint16_t)((buf[total_len-2] << 8) | buf[total_len-1]);
    return calc == got;
}
// Fallback CRC-8 (kept for the earlier reference-model tests / quick checks).
static inline uint8_t crc8_ccitt(const uint8_t *data, uint8_t len) {
    uint8_t crc = 0x00;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t b = 0; b < 8; b++)
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
    return crc;
}
static inline bool validate_crc(const uint8_t *buf, uint8_t total_len) {
    if (total_len < 2) return false;
    return crc8_ccitt(buf, total_len - 1) == buf[total_len - 1];
}

// =========================================================================
// OTA detection + packet type + UID extraction
// =========================================================================
static inline uint8_t detect_ota_version(const uint8_t *buf) {
    uint8_t ver = (buf[0] >> 6) & 0x03;
    return (ver == 1 || ver == 2) ? ver : 0;
}
static inline uint8_t packet_type(const uint8_t *buf) { return (buf[0] >> 4) & 0x03; }
// A SYNC packet carries UID[4],UID[5] in clear at the version-specific offset;
// the full 4-byte UID (our bytes[0..3] = UID[2..5]) is assembled there.
static inline bool extract_uid(const uint8_t *buf, elrs_uid *uid) {
    uint8_t ver = detect_ota_version(buf);
    uint8_t off;
    switch (ver) {
        case 1: off = OTA_V1_UID_OFFSET; break;
        case 2: off = OTA_V2_UID_OFFSET; break;
        default: return false;
    }
    uid->bytes[0] = buf[off];
    uid->bytes[1] = buf[off + 1];
    uid->bytes[2] = buf[off + 2];
    uid->bytes[3] = 0x00;          // hidden byte — filled by the 256-guess
    uid->complete = false;
    return true;
}

// =========================================================================
// Whitening + channel unpack + frame map (2.4 GHz OTA4, 16x11-bit)
// =========================================================================
static inline void dewhiten(uint8_t *payload, uint8_t len, const uint8_t uid[4]) {
    uint8_t lfsr = uid[0] ^ uid[3];
    for (uint8_t i = 0; i < len; i++) {
        payload[i] ^= lfsr;
        lfsr = (uint8_t)((lfsr >> 1) ^ (ELRS_LFSR_POLY * (lfsr & 1)));
    }
}
static inline void unpack_channels(const uint8_t *payload, uint16_t ch[N_CHANNELS]) {
    int bit_pos = 0;
    for (int c = 0; c < N_CHANNELS; c++) {
        uint16_t v = 0;
        for (int b = 0; b < 11; b++) {
            uint8_t bi = (uint8_t)(bit_pos >> 3), bb = (uint8_t)(bit_pos & 0x07);
            v |= (uint16_t)(((payload[bi] >> bb) & 1) << b);
            bit_pos++;
        }
        ch[c] = v;
    }
}
static inline void map_frame(const uint16_t ch[N_CHANNELS], rc_frame *f) {
    for (int i = 0; i < N_CHANNELS; i++) f->channels[i] = ch[i];
    f->armed       = (ch[CH_ARM] > ARM_THR);
    f->flight_mode = (uint8_t)(ch[CH_FLIGHT] / 204);
    bool thr_live  = (ch[CH_THROTTLE] > 200);
    bool stk_live  = (std::abs((int)ch[CH_ROLL]  - CH_CENTER) > DEADBAND)
                  || (std::abs((int)ch[CH_PITCH] - CH_CENTER) > DEADBAND)
                  || (std::abs((int)ch[CH_YAW]   - CH_CENTER) > DEADBAND);
    f->active      = f->armed && (thr_live || stk_live);
}
static inline rc_frame decode_rc_packet(const uint8_t *pkt, const uint8_t uid[4]) {
    uint8_t payload[ELRS_PACKET_SIZE - 2];   // strip 2-byte CRC
    memcpy(payload, pkt, ELRS_PACKET_SIZE - 2);
    dewhiten(payload, ELRS_PACKET_SIZE - 2, uid);
    rc_frame f{};
    unpack_channels(payload, f.channels);
    map_frame(f.channels, &f);
    return f;
}
