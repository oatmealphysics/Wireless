// elrs_decode.h — UHD-independent ELRS decode logic (UPSTREAM ELRS, verified
// against ExpressLRS-master source).
//
// Pure C++ (no UHD, no radio). Shared by elrs_intercept.cpp (live tool) and
// tests/test_elrs_decode.cpp (CPU-only unit test).
//
// Every constant/algorithm below is ported directly from:
//   src/lib/OTA/OTA.h        packet structs, PACKET_TYPE_*, ELRS_CRC16_POLY
//   src/lib/OTA/OTA.cpp      OtaCrcInitializer, ValidatePacketCrc, nonce logic,
//                            PackUInt11ToChannels4x10, OtaGetUidSeed
//   src/lib/CRC/crc.cpp      Crc2Byte::init/calc (table-driven CRC-16)
//   src/lib/FHSS/random.cpp  rng() LCG 214013/2531011 mod 2^31
//   src/lib/FHSS/FHSS.cpp    FHSSrandomiseFHSSsequenceBuild, ISM2G4 domain
//
// =========================================================================
// KEY PACKET LAYOUTS (OTA.h)
// =========================================================================
// OTA_Sync_s (8 bytes) — sent periodically, carries UID in clear:
//   [0] fhssIndex
//   [1] nonce
//   [2] rfRateEnum
//   [3] flags: otaProtocol in bits 6-7 (low 6 bits: switchEncMode/newTlmRatio/
//              geminiMode/... free)
//   [4] UID4
//   [5] UID5
//   [6..7] (padding / part of the 8-byte OTA frame)
//   PACKET_TYPE_SYNC = 0b10  (low 2 bits of byte[0])
//
// OTA_Packet4_s std (8 bytes) — RC data:
//   [0] type:2 (low bits) | crcHigh:6
//   [1..5] raw[5] = 4 channels x 10 bits (LSB-first bitstream, see unpack)
//   [6] switches:7 | isArmed:1
//   [7] crcLow
//   PACKET_TYPE_RCDATA = 0b00
//
// CRC-16 (Crc2Byte, poly 0x3D65, MSB-first table):
//   init = (UID[4]<<8 | UID[5]) ^ (OTA_VERSION_ID<<8)
//   nonceValidator = 0 for SYNC, OtaNonce for RC (a running 8-bit counter the
//   receiver tracks; we brute-force it 0..255 to validate RC packets)
//   calc over bytes [0 .. crcLow) with crcHigh zeroed (OTA4_CRC_CALC_LEN).
// =========================================================================

#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cmath>

// --- LoRa PHY — 2.4 GHz ELRS (ISM2G4), default RATE_LORA_2G4_150HZ ---
static constexpr uint8_t  ELRS_SYNC_WORD     = 0x12;
static constexpr uint32_t ELRS_BW_KHZ        = 800;
static constexpr uint8_t  ELRS_SF            = 7;      // default; try 5/6/8
static constexpr uint8_t  ELRS_CODING_RATE   = 8;
static constexpr uint8_t  ELRS_LFSR_POLY     = 0x69;   // whitening (OTA)

// --- FHSS domain — ISM2G4 (FHSS.cpp:22-56) ---
static constexpr uint32_t ELRS_FREQ_START_HZ = 2400400000UL;
static constexpr uint32_t ELRS_FREQ_STOP_HZ  = 2479400000UL;
static constexpr uint32_t ELRS_N_FREQ        = 80;
static constexpr uint32_t ELRS_SYNC_CHANNEL  = 40;     // freq_count / 2
static constexpr uint32_t ELRS_HOP_US        = 6666;   // 150 Hz default

// --- Packet model (OTA.h) ---
static constexpr uint8_t  ELRS_PACKET_SIZE   = 8;      // OTA4_PACKET_SIZE
static constexpr uint8_t  OTA_VERSION_ID     = 6;      // current OTA version
static constexpr uint16_t ELRS_CRC16_POLY    = 0x3D65; // ELRS_CRC16_POLY (OTA.h:190)
static constexpr uint8_t  PKT_TYPE_RCDATA    = 0b00;   // PACKET_TYPE_RCDATA
static constexpr uint8_t  PKT_TYPE_DATA      = 0b01;
static constexpr uint8_t  PKT_TYPE_SYNC      = 0b10;   // PACKET_TYPE_SYNC
static constexpr uint8_t  PKT_TYPE_LINKSTATS = 0b00;
// OTA4 RCDATA channel indices (4x 10-bit, CRSF order): 0=throttle 1=roll 2=pitch 3=yaw
static constexpr uint8_t  CH_THROTTLE        = 0;
static constexpr uint8_t  CH_ROLL            = 1;
static constexpr uint8_t  CH_PITCH           = 2;
static constexpr uint8_t  CH_YAW             = 3;

// OTA4 CRC calc length = offsetof(OTA_Packet4_s, crcLow) = 7 bytes [0..6]
static constexpr uint8_t  OTA4_CRC_CALC_LEN  = 7;

// --- PRNG — EXACT port of src/lib/FHSS/random.cpp ---
static inline uint16_t elrs_rng(uint32_t &seed) {
    const uint64_t m = 2147483648ULL, a = 214013ULL, c = 2531011ULL;
    seed = (uint32_t)((a * (uint64_t)seed + c) % m);
    return (uint16_t)(seed >> 16);
}
static inline uint8_t elrs_rngN(uint32_t &seed, uint8_t max) {
    return (uint8_t)(elrs_rng(seed) % max);
}

// --- UID -> PRNG seed — OtaGetUidSeed() (OTA.cpp:41). UID is 6 bytes; seed
//     uses UID[2],[3],[4],[5^OTA_VERSION_ID]. Our elrs_uid.bytes[4] holds
//     UID[2..5] (bytes[0]=UID[2]..bytes[3]=UID[5]).
static inline uint32_t uid_seed(const uint8_t uid[4]) {
    return ((uint32_t)uid[0] << 24) | ((uint32_t)uid[1] << 16)
         | ((uint32_t)uid[2] << 8)  | (uint32_t)(uid[3] ^ OTA_VERSION_ID);
}

// --- FHSS sequence builder — FHSSrandomiseFHSSsequenceBuild() (FHSS.cpp:139) ---
static const uint16_t ELRS_SEQ_LEN = 256;   // FHSS_SEQUENCE_LEN
static inline uint16_t build_fhss_sequence(const uint8_t uid[4], uint8_t seq[ELRS_SEQ_LEN]) {
    const uint16_t freqCount = (uint16_t)ELRS_N_FREQ;
    const uint8_t  syncChannel = (uint8_t)ELRS_SYNC_CHANNEL;
    const uint16_t sequenceCount = (ELRS_SEQ_LEN / freqCount) * freqCount;
    uint32_t seed = uid_seed(uid);
    for (uint16_t i = 0; i < sequenceCount; i++) {
        if (i % freqCount == 0)                 seq[i] = syncChannel;
        else if (i % freqCount == syncChannel)  seq[i] = 0;
        else                                    seq[i] = (uint8_t)(i % freqCount);
    }
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
static inline uint32_t freq_for_index(uint8_t idx) {
    uint64_t spread = (uint64_t)(ELRS_FREQ_STOP_HZ - ELRS_FREQ_START_HZ);
    return (uint32_t)(ELRS_FREQ_START_HZ + (spread * idx) / (ELRS_N_FREQ - 1));
}
static inline void generate_hop_table(const uint8_t uid[4], uint32_t table[ELRS_N_FREQ]) {
    uint8_t seq[ELRS_SEQ_LEN];
    build_fhss_sequence(uid, seq);
    for (uint32_t i = 0; i < ELRS_N_FREQ; i++) table[i] = freq_for_index(seq[i]);
}

// --- CRC — EXACT port of Crc2Byte (crc.cpp), bit-width-generic.
// OTA4 (the 8-byte RC/SYNC packet we target) uses a 14-bit CRC, poly 0x2E57.
// (OTA8/full uses 16-bit, poly 0x3D65 — see OTA.cpp:560,577.) ---
static constexpr uint16_t ELRS_CRC14_POLY    = 0x2E57; // ELRS_CRC14_POLY (OTA.h:189)
static constexpr uint8_t  ELRS_CRC_BITS      = 14;     // OTA4
// Table build: crc = i << (bits-8); for 8 bits: crc = (crc<<1)^((crc & (1<<(bits-1)))?poly:0)
static inline void build_crc_table(uint16_t tab[256], uint8_t bits, uint16_t poly) {
    const uint16_t highbit = (uint16_t)(1 << (bits - 1));
    for (int i = 0; i < 256; i++) {
        uint16_t crc = (uint16_t)(i << (bits - 8));
        for (int j = 0; j < 8; j++)
            crc = (uint16_t)((crc << 1) ^ ((crc & highbit) ? poly : 0));
        tab[i] = crc;
    }
}
static inline uint16_t crc_calc(const uint16_t tab[256], uint8_t bits,
                                const uint8_t *data, uint8_t len, uint16_t crc) {
    const uint16_t mask = (uint16_t)((1 << bits) - 1);
    const int shift = bits - 8;
    while (len--)
        crc = (uint16_t)((crc << 8) ^ tab[((crc >> shift) ^ *data++) & 0xFF]);
    return crc & mask;
}
// Convenience: build+calc for the OTA4 14-bit CRC.
static inline uint16_t crc14_calc(const uint8_t *data, uint8_t len, uint16_t crc) {
    static uint16_t tab[256];
    static bool inited = false;
    if (!inited) { build_crc_table(tab, ELRS_CRC_BITS, ELRS_CRC14_POLY); inited = true; }
    return crc_calc(tab, ELRS_CRC_BITS, data, len, crc);
}
// CRC init — OtaUpdateCrcInitFromUid() (OTA.cpp:28): (UID[4]<<8|UID[5])^(ov<<8).
// Our uid[4]=uid[2], uid[5]=uid[3].
static inline uint16_t crc_init_from_uid(const uint8_t uid[4]) {
    return (uint16_t)(((uint16_t)uid[2] << 8 | uid[3]) ^ ((uint16_t)OTA_VERSION_ID << 8));
}
// Validate an OTA4 packet. For RC, the receiver's running OtaNonce is unknown
// to a passive interceptor, so we brute-force nonceValidator 0..255 (a SYNC
// packet uses nonceValidator=0). Returns true + fills *nonce_out on success.
static inline bool validate_ota4(const uint8_t pkt[8], const uint8_t uid[4],
                                 bool is_sync, uint8_t *nonce_out) {
    uint16_t init = crc_init_from_uid(uid);
    // OTA4 stored CRC (14-bit): crcHigh:6 = top 6 bits (bits 6-13) into byte0[7:2],
    // crcLow = bottom 8 bits (bits 0-7) in byte7. inCRC = (crcHigh<<8)+crcLow.
    uint16_t stored = (uint16_t)(((uint16_t)(pkt[0] >> 2) & 0x3F) << 8) | pkt[7];
    uint8_t buf[7];
    memcpy(buf, pkt, 7);
    buf[0] &= 0x03;   // zero crcHigh (keep type:2 low bits) — calc before it's ORed in
    if (is_sync) {
        if (crc14_calc(buf, OTA4_CRC_CALC_LEN, init) == stored) {
            if (nonce_out) *nonce_out = 0;
            return true;
        }
        return false;
    }
    for (uint8_t nonce = 0; nonce < 256; nonce++) {
        if (crc14_calc(buf, OTA4_CRC_CALC_LEN, (uint16_t)(init ^ nonce)) == stored) {
            if (nonce_out) *nonce_out = nonce;
            return true;
        }
    }
    return false;
}

// --- Packet type + OTA version + UID extraction ---
// PACKET_TYPE_SYNC = 0b10 in the LOW 2 bits of byte[0] (OTA.h:22, :57).
static inline uint8_t packet_type(const uint8_t *buf) { return buf[0] & 0x03; }
// otaProtocol = bits 6-7 of byte[3] (OTA.h:36).
static inline uint8_t ota_protocol(const uint8_t *buf) { return (buf[3] >> 6) & 0x03; }
// SYNC carries UID4,UID5 in clear at [4],[5] (OTA_Sync_s, OTA.h:38-39).
// elrs_uid.bytes holds UID[2..5]: bytes[2]=UID4, bytes[3]=UID5. For a bound
// link UID[0..2]=0, so bytes[0..1]=0 (UID[2],UID[3]).
static inline bool extract_uid(const uint8_t *buf, uint8_t uid[4]) {
    if (packet_type(buf) != PKT_TYPE_SYNC) return false;
    uid[0] = 0x00;   // UID[2] (0 when bound)
    uid[1] = 0x00;   // UID[3] (0 when bound)
    uid[2] = buf[4]; // UID4
    uid[3] = buf[5]; // UID5
    return true;
}

struct elrs_uid { uint8_t bytes[4]; bool complete; };
struct rc_frame {
    uint16_t channels[4];   // 4x 10-bit channels (OTA4 RCDATA)
    uint8_t  switches;
    bool     armed;
    bool     active;
    uint8_t  flight_mode;
};

// --- 4x10-bit channel unpack — inverse of PackUInt11ToChannels4x10 (OTA.cpp:94) ---
// 5-byte LSB-first bitstream: 4 channels x 10 bits packed contiguously.
static inline void unpack_channels_4x10(const uint8_t raw[5], uint16_t ch[4]) {
    int bit_pos = 0;
    for (int c = 0; c < 4; c++) {
        uint16_t v = 0;
        for (int b = 0; b < 10; b++) {
            uint8_t bi = (uint8_t)(bit_pos >> 3), bb = (uint8_t)(bit_pos & 0x07);
            v |= (uint16_t)(((raw[bi] >> bb) & 1) << b);
            bit_pos++;
        }
        ch[c] = v;
    }
}
// Decode an OTA4 RCDATA packet: 4x10-bit channels + switches + isArmed.
static inline rc_frame decode_rc_packet(const uint8_t pkt[8], const uint8_t uid[4]) {
    (void)uid;
    rc_frame f{};
    unpack_channels_4x10(&pkt[1], f.channels);   // raw[5] at pkt[1..5]
    f.switches = pkt[6] & 0x7F;                  // switches:7
    f.armed    = (bool)((pkt[6] >> 7) & 1);      // isArmed:1
    f.active   = f.armed && (f.channels[CH_THROTTLE] > 200);  // throttle = ch0
    f.flight_mode = 0;
    return f;
}
