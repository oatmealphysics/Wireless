// demod_cli.cpp — read a .iq file, run the C++ LoRa demod, dump result as JSON.
// No UHD. Used by cross_validate_demod.py to prove the C++ demod matches the
// proven numpy reference.  Build: g++ -O2 -std=c++17 demod_cli.cpp -o demod_cli
#include "../lora_demod_cpp.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>
#include <complex>

int main(int argc, char **argv) {
    if (argc < 6) {
        std::fprintf(stderr, "usage: %s iq_file sf bw_hz fs out.json\n", argv[0]);
        return 2;
    }
    std::string iq_path = argv[1];
    int sf = atoi(argv[2]);
    uint32_t bw_hz = (uint32_t)atol(argv[3]);
    double fs = atof(argv[4]);
    std::string out_json = argv[5];

    std::ifstream f(iq_path, std::ios::binary);
    if (!f) { std::fprintf(stderr, "cannot open %s\n", iq_path.c_str()); return 1; }
    f.seekg(0, std::ios::end);
    size_t bytes = f.tellg();
    f.seekg(0, std::ios::beg);
    size_t n = bytes / sizeof(std::complex<float>);
    std::vector<std::complex<float>> iq(n);
    f.read((char *)iq.data(), (std::streamsize)bytes);
    size_t got = f.gcount() / sizeof(std::complex<float>);

    std::vector<uint8_t> byt = lora::demod_iq(iq.data(), got, sf, bw_hz, fs, false);
    // also recover raw symbols for validation
    int sps = (int)std::lround(fs / (double)bw_hz);
    auto d = lora::decimate(iq.data(), got, sps);
    int N = 1 << sf;
    std::vector<int> syms;
    // reuse the same preamble-end heuristic (simple: scan whole buffer)
    for (size_t pos = 0; pos + (size_t)N <= d.size() && syms.size() < 128; pos += N)
        syms.push_back(lora::demod_sym(sf, &d[pos]));

    std::printf("{\"n\":%lld, \"sps\":%d, \"bytes\":[", (long long)got, sps);
    for (size_t i = 0; i < byt.size(); i++) std::printf("%s%d", (i ? "," : ""), (int)byt[i]);
    std::printf("], \"syms\":[");
    for (size_t i = 0; i < syms.size(); i++) std::printf("%s%d", (i ? "," : ""), syms[i]);
    std::printf("]}\n");
    return 0;
}
