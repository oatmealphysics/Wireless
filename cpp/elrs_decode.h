// elrs_decode.h — UHD-independent ELRS decode logic.
//
// Pure C++ (no UHD, no radio). Shared by elrs_intercept.cpp (the live tool)
// and tests/test_elrs_decode.cpp (the CPU-only unit test), so the decode
// logic is validated on a normal build host before any live round-trip.
//
// Constants and algorithms mirror the PROVEN reference:
//   LoRa-ELRS-Interceptor-Decoder/firmware/include/elrs_common.h + phase*.cpp

#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cmath>

// --- LoRa PHY (ELRS 900 MHz) ---
static constexpr uint8_t  ELRS_SYNC_WORD     = 0x12;
static constexpr uint32_t ELRS_BW_KHZ        = 500;
static constexpr uint8_t  ELRS_CODING_RATE   = 5;
static constexpr uint8_t  ELRS_SF            = 7;   // set from sdr_characterize.py

// --- FHSS (ELRS/src/lib/FHSS/FHSS.cpp as used by the reference) ---
static constexpr uint32_t ELRS_FREQ_BASE_HZ  = 903500000UL;
static constexpr uint32_t ELRS_FREQ_STEP_HZ  = 500000UL;
static constexpr uint32_t ELRS_N_FREQ        = 13;
static constexpr uint32_t ELRS_LCG_MULT      = 2891336453UL;
static constexpr uint32_t ELRS_LCG_INC       = 1UL;
static constexpr uint8_t  ELRS_LFSR_POLY     = 0x69;

// --- Packet model ---
static constexpr uint8_t  ELRS_PACKET_SIZE   = 8;
static constexpr uint8_t  ELRS_CRC_SIZE      = 1;
static constexpr uint8_t  PKT_TYPE_RC        = 0x00;
static constexpr uint8_t  PKT_TYPE_SYNC      = 0x01;
static constexpr uint8_t  OTA_VERSION_1      = 1;
static constexpr uint8_t  OTA_VERSION_2      = 2;
static constexpr uint8_t  OTA_V1_UID_OFFSET  = 2;
static constexpr uint8_t  OTA_V2_UID_OFFSET  = 3;

// --- RC channel mapping ---
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

static inline uint64_t hop_interval_us() {
    return ((1UL << ELRS_SF) * 1000000UL / (ELRS_BW_KHZ * 1000UL)) * 12UL;
}

struct elrs_uid { uint8_t bytes[4]; bool complete; };
struct rc_frame {
    uint16_t channels[N_CHANNELS];
    bool     armed;
    bool     active;
    uint8_t  flight_mode;
};

// --- Phase 1: CRC + OTA detection + UID extraction ---
inline uint8_t crc8_ccitt(const uint8_t *data, uint8_t len) {
    uint8_t crc = 0x00;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t b = 0; b < 8; b++)
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
    return crc;
}
inline bool validate_crc(const uint8_t *buf, uint8_t total_len) {
    if (total_len < 2) return false;
    return crc8_ccitt(buf, total_len - 1) == buf[total_len - 1];
}
inline uint8_t detect_ota_version(const uint8_t *buf) {
    uint8_t ver = (buf[0] >> 6) & 0x03;
    return (ver == OTA_VERSION_1 || ver == OTA_VERSION_2) ? ver : 0;
}
inline uint8_t packet_type(const uint8_t *buf) { return (buf[0] >> 4) & 0x03; }
inline bool extract_uid(const uint8_t *buf, elrs_uid *uid) {
    uint8_t ver = detect_ota_version(buf);
    uint8_t off;
    switch (ver) {
        case OTA_VERSION_1: off = OTA_V1_UID_OFFSET; break;
        case OTA_VERSION_2: off = OTA_V2_UID_OFFSET; break;
        default: return false;
    }
    uid->bytes[0] = buf[off];
    uid->bytes[1] = buf[off + 1];
    uid->bytes[2] = buf[off + 2];
    uid->bytes[3] = 0x00;
    uid->complete = false;
    return true;
}

// --- Phase 2: Fisher-Yates hop table + freq lookup ---
inline void generate_hop_table(const elrs_uid *uid, uint32_t table[ELRS_N_FREQ]) {
    for (uint32_t i = 0; i < ELRS_N_FREQ; i++)
        table[i] = ELRS_FREQ_BASE_HZ + i * ELRS_FREQ_STEP_HZ;
    uint32_t seed = (uint32_t)uid->bytes[0]
                  | ((uint32_t)uid->bytes[1] << 8)
                  | ((uint32_t)uid->bytes[2] << 16)
                  | ((uint32_t)uid->bytes[3] << 24);
    for (int i = (int)ELRS_N_FREQ - 1; i > 0; i--) {
        seed = seed * ELRS_LCG_MULT + ELRS_LCG_INC;
        uint32_t j = seed % (uint32_t)(i + 1);
        std::swap(table[i], table[j]);
    }
}
inline uint32_t freq_at(const elrs_uid *uid, uint8_t idx) {
    uint32_t t[ELRS_N_FREQ];
    generate_hop_table(uid, t);
    return t[idx % ELRS_N_FREQ];
}

// --- Phase 4: dewhiten + channel unpack + frame map ---
inline void dewhiten(uint8_t *payload, uint8_t len, const elrs_uid *uid) {
    uint8_t lfsr = uid->bytes[0] ^ uid->bytes[3];
    for (uint8_t i = 0; i < len; i++) {
        payload[i] ^= lfsr;
        lfsr = (uint8_t)((lfsr >> 1) ^ (ELRS_LFSR_POLY * (lfsr & 1)));
    }
}
inline void unpack_channels(const uint8_t *payload, uint16_t ch[N_CHANNELS]) {
    int bit_pos = 0;
    for (int c = 0; c < N_CHANNELS; c++) {
        uint16_t v = 0;
        for (int b = 0; b < 11; b++) {
            uint8_t bi = bit_pos >> 3, bb = bit_pos & 0x07;
            v |= (uint16_t)(((payload[bi] >> bb) & 1) << b);
            bit_pos++;
        }
        ch[c] = v;
    }
}
inline void map_frame(const uint16_t ch[N_CHANNELS], rc_frame *f) {
    for (int i = 0; i < N_CHANNELS; i++) f->channels[i] = ch[i];
    f->armed       = (ch[CH_ARM] > ARM_THR);
    f->flight_mode = (uint8_t)(ch[CH_FLIGHT] / 204);
    bool thr_live  = (ch[CH_THROTTLE] > 200);
    bool stk_live  = (std::abs((int)ch[CH_ROLL]  - CH_CENTER) > DEADBAND)
                  || (std::abs((int)ch[CH_PITCH] - CH_CENTER) > DEADBAND)
                  || (std::abs((int)ch[CH_YAW]   - CH_CENTER) > DEADBAND);
    f->active      = f->armed && (thr_live || stk_live);
}
inline rc_frame decode_rc_packet(const uint8_t *pkt, const elrs_uid *uid) {
    uint8_t payload[ELRS_PACKET_SIZE - ELRS_CRC_SIZE];
    memcpy(payload, pkt, ELRS_PACKET_SIZE - ELRS_CRC_SIZE);
    dewhiten(payload, ELRS_PACKET_SIZE - ELRS_CRC_SIZE, uid);
    rc_frame f{};
    unpack_channels(payload, f.channels);
    map_frame(f.channels, &f);
    return f;
}
