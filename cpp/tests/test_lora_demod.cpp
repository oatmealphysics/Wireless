// test_lora_demod.cpp — CPU-only test: numpy-generated IQ -> C++ demod -> payload.
// No UHD. Proves lora::demod_iq (the C++ port) recovers the exact payload bytes
// that the proven numpy reference produced, i.e. the C++ demod is a faithful
// port. Build: g++ -O2 -std=c++17 test_lora_demod.cpp -o test_lora_demod
// Run:   ./test_lora_demod ~/xval.iq <payload_hex> <sf> <bw_hz> <fs>
#include "../lora_demod_cpp.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <vector>
#include <complex>
#include <cstdint>

int main(int argc, char **argv) {
    if (argc < 6) {
        std::fprintf(stderr, "usage: %s iq sf bw_hz fs payload_hex\n", argv[0]);
        return 2;
    }
    std::string iq_path = argv[1];
    int sf = atoi(argv[2]);
    uint32_t bw_hz = (uint32_t)atol(argv[3]);
    double fs = atof(argv[4]);
    std::string payload_hex = argv[5];

    std::ifstream f(iq_path, std::ios::binary);
    f.seekg(0, std::ios::end);
    size_t bytes = f.tellg();
    f.seekg(0, std::ios::beg);
    std::vector<std::complex<float>> iq(bytes / 8);
    f.read((char *)iq.data(), (std::streamsize)bytes);
    size_t n = f.gcount() / 8;

    std::vector<uint8_t> out = lora::demod_iq(iq.data(), n, sf, bw_hz, fs, false);

    // expected payload bytes from hex (e.g. "010203...0c")
    std::vector<uint8_t> expect;
    for (size_t i = 0; i + 1 < payload_hex.size(); i += 2)
        expect.push_back((uint8_t)std::stoul(payload_hex.substr(i, 2), nullptr, 16));

    std::printf("recovered bytes (%zu):", out.size());
    for (size_t i = 0; i < out.size() && i < 32; i++) std::printf(" %02x", out[i]);
    std::printf("\nexpected (%zu):", expect.size());
    for (size_t i = 0; i < expect.size(); i++) std::printf(" %02x", expect[i]);
    std::printf("\n");

    // The payload must appear as a contiguous run in the recovered bytes.
    bool found = false;
    if (out.size() >= expect.size())
        for (size_t i = 0; i + expect.size() <= out.size(); i++) {
            bool m = true;
            for (size_t k = 0; k < expect.size(); k++) if (out[i+k] != expect[k]) { m = false; break; }
            if (m) { found = true; break; }
        }
    std::printf(found ? "C++ DEMOD RECOVERS PAYLOAD ✓\n" : "C++ DEMOD PAYLOAD MISMATCH ✗\n");
    return found ? 0 : 1;
}
