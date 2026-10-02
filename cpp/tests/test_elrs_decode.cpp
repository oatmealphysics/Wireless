// tests/test_elrs_decode.cpp — CPU-side unit test for the ELRS decode logic.
//
// Compiles and runs WITHOUT UHD (no radio, no B205mini). It validates the
// parts of elrs_intercept.cpp that are pure logic: CRC-8, OTA detection,
// UID extraction, Fisher-Yates hop table, freq_at, dewhiten, channel
// unpack, frame map, and the 256-guess seed check.
//
// Build:   g++ -O2 -std=c++17 test_elrs_decode.cpp -o test_elrs_decode
// Run:     ./test_elrs_decode
//
// Exit 0 = all pass. This is the piece that must be proven before any
// live round-trip; the UHD capture loop is not exercised here.

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

// Pull in ONLY the decode section of elrs_intercept.cpp. We do this by
// re-declaring the constants/functions (kept in sync with the source) rather
// than #including the UHD header, so this test needs no UHD.
#include "../elrs_decode.h"

static int g_fail = 0;
#define CHECK(cond, msg) do { \
    if (cond) { std::printf("  PASS  %s\n", msg); } \
    else      { std::printf("  FAIL  %s\n", msg); g_fail++; } \
} while (0)

// Build a synthetic RC packet that is self-consistent under the model:
//   - 8 bytes, type RC (0x00) in bits [5:4], OTA v2 (0b10) in bits [7:6]
//   - UID at offset 3 (OTA_V2_UID_OFFSET)
//   - payload whitened with the LFSR
//   - CRC-8 over bytes[0..6] appended at byte[7]
// The known channel set is packed into the (whitened) payload, so decoding
// it back with the same UID must recover the channels exactly.
static void build_synthetic_rc(const uint8_t uid4[4], uint16_t ch[N_CHANNELS],
                               uint8_t pkt[ELRS_PACKET_SIZE]) {
    // Header byte: OTA v2 (top2=0b10) | type RC (0b00 in bits[5:4])
    uint8_t hdr = (OTA_VERSION_2 << 6) | (PKT_TYPE_RC << 4);
    // 7-byte CRC'd region: hdr, pad, pad, uid0..2, payload start
    uint8_t region[ELRS_PACKET_SIZE - ELRS_CRC_SIZE];
    region[0] = hdr;
    region[1] = 0x00;
    region[2] = 0x00;
    region[3] = uid4[0];
    region[4] = uid4[1];
    region[5] = uid4[2];
    region[6] = 0x00;

    // Build the plaintext RC payload (16 x 11-bit channels = 22 bytes), but
    // ELRS_PACKET_SIZE-ELRS_CRC_SIZE = 7 < 22, so a full 16-channel frame does
    // NOT fit in the 8-byte model. The reference firmware has the same
    // constraint: it packs only as many channels as the payload allows.
    // Here we pack channels into the available payload bytes after the header.
    // For the test we treat bytes[3..6] as UID (already placed) and use the
    // remaining low bits minimally; instead, we validate the unpack/dewhiten
    // round-trip on a DEDICATED 22-byte buffer to avoid the size clash.
    (void)region;
    (void)ch;
    // Placeholder: the real round-trip is done in the dedicated buffer test.
    memset(pkt, 0, ELRS_PACKET_SIZE);
}

int main() {
    std::printf("ELRS decode unit test (CPU-only, no UHD)\n");

    // --- CRC-8/CCITT sanity (known test vector) ---
    // "123456789" -> 0xF4 for CRC-8/CCITT (poly 0x07, init 0x00)
    {
        const uint8_t v[] = "123456789";
        uint8_t c = crc8_ccitt(v, 9);
        CHECK(c == 0xF4, "CRC-8/CCITT of '123456789' == 0xF4");
    }

    // --- validate_crc: self-consistent packet passes ---
    {
        uint8_t p[ELRS_PACKET_SIZE] = {0};
        for (int i = 0; i < ELRS_PACKET_SIZE - 1; i++) p[i] = (uint8_t)(i * 7 + 3);
        p[ELRS_PACKET_SIZE - 1] = crc8_ccitt(p, ELRS_PACKET_SIZE - 1);
        CHECK(validate_crc(p, ELRS_PACKET_SIZE), "validate_crc passes on self-consistent packet");
        p[2] ^= 0xFF;  // corrupt
        CHECK(!validate_crc(p, ELRS_PACKET_SIZE), "validate_crc rejects corrupted packet");
    }

    // --- OTA version detection ---
    {
        uint8_t b2[8] = {0}; b2[0] = (OTA_VERSION_2 << 6);
        CHECK(detect_ota_version(b2) == OTA_VERSION_2, "detect OTA v2 from header");
        uint8_t b1[8] = {0}; b1[0] = (OTA_VERSION_1 << 6);
        CHECK(detect_ota_version(b1) == OTA_VERSION_1, "detect OTA v1 from header");
        uint8_t bx[8] = {0}; bx[0] = (0x03 << 6);
        CHECK(detect_ota_version(bx) == 0, "reject unknown OTA version");
    }

    // --- UID extraction (OTA v2, offset 3) ---
    {
        uint8_t b[ELRS_PACKET_SIZE] = {0};
        b[0] = (OTA_VERSION_2 << 6) | (PKT_TYPE_SYNC << 4);
        b[OTA_V2_UID_OFFSET + 0] = 0xAB;
        b[OTA_V2_UID_OFFSET + 1] = 0xCD;
        b[OTA_V2_UID_OFFSET + 2] = 0xEF;
        elrs_uid uid;
        bool ok = extract_uid(b, &uid);
        CHECK(ok && uid.bytes[0] == 0xAB && uid.bytes[1] == 0xCD && uid.bytes[2] == 0xEF
              && uid.bytes[3] == 0x00 && !uid.complete,
              "extract_uid reads 3 bytes, zeroes byte[3], complete=false");
    }

    // --- Fisher-Yates hop table: deterministic + permutation ---
    {
        elrs_uid uid = {{0x12, 0x34, 0x56, 0x78}, true};
        uint32_t t1[ELRS_N_FREQ], t2[ELRS_N_FREQ];
        generate_hop_table(&uid, t1);
        generate_hop_table(&uid, t2);
        bool same = true;
        for (int i = 0; i < ELRS_N_FREQ; i++) if (t1[i] != t2[i]) same = false;
        CHECK(same, "hop table deterministic for same UID");

        // Must be a permutation of the 13 sequential channels
        bool is_perm = true;
        for (uint32_t i = 0; i < ELRS_N_FREQ; i++) {
            bool found = false;
            for (int k = 0; k < ELRS_N_FREQ; k++)
                if (t1[k] == ELRS_FREQ_BASE_HZ + i * ELRS_FREQ_STEP_HZ) found = true;
            if (!found) is_perm = false;
        }
        CHECK(is_perm, "hop table is a permutation of the 13 channels");

        // Different UID -> (almost certainly) different table
        elrs_uid uid2 = {{0x12, 0x34, 0x56, 0x79}, true};
        uint32_t t3[ELRS_N_FREQ];
        generate_hop_table(&uid2, t3);
        bool diff = false;
        for (int i = 0; i < ELRS_N_FREQ; i++) if (t1[i] != t3[i]) diff = true;
        CHECK(diff, "different UID byte[3] -> different hop table");
    }

    // --- freq_at wraps and returns a valid channel ---
    {
        elrs_uid uid = {{0x12, 0x34, 0x56, 0x78}, true};
        uint32_t f = freq_at(&uid, 20);  // 20 % 13 == 7
        uint32_t t[ELRS_N_FREQ];
        generate_hop_table(&uid, t);
        CHECK(f == t[7], "freq_at(idx) == table[idx % N]");
    }

    // --- dewhiten is its own inverse (XOR) ---
    {
        elrs_uid uid = {{0x11, 0x22, 0x33, 0x44}, true};
        uint8_t orig[8], a[8];
        for (int i = 0; i < 8; i++) orig[i] = a[i] = (uint8_t)(i * 13 + 5);
        dewhiten(a, 8, &uid);
        bool changed = false;
        for (int i = 0; i < 8; i++) if (a[i] != orig[i]) changed = true;
        CHECK(changed, "dewhiten changes payload");
        dewhiten(a, 8, &uid);   // second application restores (XOR symmetry)
        CHECK(memcmp(a, orig, 8) == 0, "dewhiten twice == identity");
    }

    // --- channel unpack: round-trip a known 16-channel frame ---
    {
        uint16_t in_ch[N_CHANNELS];
        for (int c = 0; c < N_CHANNELS; c++) in_ch[c] = (uint16_t)(1000 + c * 10);
        // Pack LSB-first into 22 bytes (16*11=176 bits)
        uint8_t buf[22] = {0};
        int bit_pos = 0;
        for (int c = 0; c < N_CHANNELS; c++)
            for (int b = 0; b < 11; b++) {
                uint8_t bi = bit_pos >> 3, bb = bit_pos & 7;
                if (in_ch[c] & (1 << b)) buf[bi] |= (1 << bb);
                bit_pos++;
            }
        uint16_t out_ch[N_CHANNELS] = {0};
        unpack_channels(buf, out_ch);
        bool match = true;
        for (int c = 0; c < N_CHANNELS; c++) if (out_ch[c] != in_ch[c]) match = false;
        CHECK(match, "unpack_channels round-trips 16 x 11-bit channels");

        // frame map: arm threshold
        rc_frame f{};
        in_ch[CH_ARM] = ARM_THR + 1;
        in_ch[CH_THROTTLE] = 300;   // above idle
        for (int c = 0; c < N_CHANNELS; c++) f.channels[c] = in_ch[c];
        map_frame(f.channels, &f);
        CHECK(f.armed, "map_frame flags armed when CH_ARM > threshold");
        CHECK(f.active, "map_frame flags active when armed + throttle live");
    }

    // --- 256-guess seed check ---
    // The full 32-bit UID seed yields 256 DISTINCT Fisher-Yates tables (only
    // the true byte reproduces the true table). A SINGLE hop position is NOT
    // unique (many byte3 values map to the same freq at a given index) — that
    // is why the reference confirms a seed with a real CRC-valid RC packet
    // observed on the predicted freq, not by freq-match alone.
    {
        elrs_uid true_uid = {{0xDE, 0xAD, 0xBE, 0xEF}, true};
        uint32_t true_t[ELRS_N_FREQ];
        generate_hop_table(&true_uid, true_t);

        // (a) exactly one byte3 reproduces the ENTIRE table
        int table_hits = 0;
        for (int g = 0; g < 256; g++) {
            uint32_t c[ELRS_N_FREQ];
            elrs_uid cand = {{0xDE, 0xAD, 0xBE, (uint8_t)g}, true};
            generate_hop_table(&cand, c);
            bool same = true;
            for (int i = 0; i < ELRS_N_FREQ; i++) if (c[i] != true_t[i]) same = false;
            if (same) table_hits++;
        }
        CHECK(table_hits == 1, "256-guess: exactly one UID byte reproduces the full hop table");

        // (b) a single index is ambiguous (>=2 candidates), so freq-match alone
        //     is insufficient — document the property the reference relies on.
        uint8_t next_idx = 5;
        int idx_hits = 0;
        for (int g = 0; g < 256; g++) {
            elrs_uid cand = {{0xDE, 0xAD, 0xBE, (uint8_t)g}, true};
            if (freq_at(&cand, next_idx) == true_t[next_idx]) idx_hits++;
        }
        CHECK(idx_hits >= 2, "single-hop freq is ambiguous (needs CRC-RC confirmation)");
    }

    if (g_fail) { std::printf("\n%d FAILURES\n", g_fail); return 1; }
    std::printf("\nALL DECODE TESTS PASS\n");
    return 0;
}
