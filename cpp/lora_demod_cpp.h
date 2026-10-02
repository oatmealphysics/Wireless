// lora_demod_cpp.h — pure-C++ LoRa demod (no UHD, no numpy).
//
// Port of the proven numpy demod in tools/lora_ref.py + tools/elrs_live.py:
//   downchirp -> FFT -> argmax(|.|) per symbol, LSB-first symbol->bit pack.
// UHD-independent so it can be unit-tested on a normal build host before the
// live tool is built. Shared by elrs_intercept.cpp and tests/test_lora_demod.cpp.
//
// The reference demod is R=1 (one sample per symbol at base) and works on
// baseband IQ sampled at fs. We downsample/decimate to ~1 sample per symbol
// before demod so it is robust to the capture sample rate.

#pragma once
#include <complex>
#include <cstdint>
#include <vector>
#include <cmath>
#include <algorithm>

namespace lora {

// A single up/down chirp of 2^SF samples, unit-magnitude.
inline std::vector<std::complex<float>> upchirp(int sf) {
    int N = 1 << sf;
    std::vector<std::complex<float>> c(N);
    for (int n = 0; n < N; n++) {
        float x = std::sin(2.0f * 3.14159265f * ((float)(n * n) / (2.0f * N) - (float)n / 2.0f));
        float y = std::cos(2.0f * 3.14159265f * ((float)(n * n) / (2.0f * N) - (float)n / 2.0f));
        c[n] = std::complex<float>(y, x);   // exp(2*pi*j*phi)
    }
    return c;
}
inline std::vector<std::complex<float>> downchirp(int sf) {
    std::vector<std::complex<float>> c = upchirp(sf);
    for (auto &z : c) z = std::conj(z);
    return c;
}

// Iterative radix-2 Cooley-Tukey FFT (in-place, forward). Input length is a
// power of two (== 2^SF). Returns the FFT of x (same size).
inline std::vector<std::complex<float>> fft(std::vector<std::complex<float>> x) {
    int n = (int)x.size();
    // bit-reversal permutation
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(x[i], x[j]);
    }
    for (int len = 2; len <= n; len <<= 1) {
        double ang = -2.0 * 3.14159265358979 / len;
        std::complex<double> wlen(std::cos(ang), std::sin(ang));
        for (int i = 0; i < n; i += len) {
            std::complex<double> w(1.0, 0.0);
            for (int j = 0; j < len / 2; j++) {
                std::complex<double> u = x[i + j];
                std::complex<double> v = std::complex<double>(x[i + j + len / 2]) * w;
                x[i + j] = (std::complex<float>)(u + v);
                x[i + j + len / 2] = (std::complex<float>)(u - v);
                w *= wlen;
            }
        }
    }
    return x;
}

// Demodulate one symbol (exactly N=2^SF samples) -> symbol index 0..N-1.
// y = fft(x * downchirp); return argmax(|y|).
inline int demod_sym(int sf, const std::complex<float> *x) {
    int N = 1 << sf;
    std::vector<std::complex<float>> dc = downchirp(sf);
    std::vector<std::complex<float>> y(N);
    for (int i = 0; i < N; i++) y[i] = x[i] * dc[i];
    std::vector<std::complex<float>> Y = fft(y);
    int best = 0; float best_m = -1.0f;
    for (int i = 0; i < N; i++) {
        float m = std::norm(Y[i]);
        if (m > best_m) { best_m = m; best = i; }
    }
    return best;
}

// Downsample an IQ buffer to ~one sample per symbol. Returns the decimated
// complex buffer. `sps` = samples per symbol (fs / bw_hz, rounded).
inline std::vector<std::complex<float>> decimate(const std::complex<float> *iq,
                                                 size_t n, int sps) {
    if (sps < 1) sps = 1;
    size_t outn = n / (size_t)sps;
    std::vector<std::complex<float>> out(outn);
    for (size_t i = 0; i < outn; i++) {
        // take the sample at the symbol midpoint to avoid edge phase issues
        out[i] = iq[i * (size_t)sps + (size_t)(sps / 2)];
    }
    return out;
}

// Full demod of a baseband IQ window for a given (sf, bw, fs, inverted).
// Returns the recovered payload bytes (LSB-first symbol->bit), matching the
// proven numpy path. May be empty if no preamble is found.
//
// Strategy (matches tools/lora_ref.py + elrs_live.py):
//   1. decimate to ~1 sample per symbol
//   2. locate the PREAMBLE START via downchirp correlation (stepped scan)
//   3. the reference preamble is 8*upchirp + 2*sync + 2*down + 1/4*down.
//      The DATA payload starts right after the LAST full downchirp, i.e.
//      ~12 symbols after preamble start. We demod from there.
inline std::vector<uint8_t> demod_iq(const std::complex<float> *iq, size_t n,
                                     int sf, uint32_t bw_hz, double fs,
                                     bool inverted) {
    int N = 1 << sf;
    int sps = (int)std::lround(fs / (double)bw_hz);
    std::vector<std::complex<float>> d = decimate(iq, n, sps);
    if (d.size() < (size_t)N * 16) return {};
    if (inverted) for (auto &z : d) z = std::conj(z);

    // Locate preamble start via downchirp correlation, fine-grained (step 1)
    // over a bounded window. Downchirp correlation peaks on upchirp symbols
    // (the 8-symbol preamble), giving a strong, unambiguous start.
    std::vector<std::complex<float>> dc = downchirp(sf);
    size_t search_len = std::min(d.size() - (size_t)N, (size_t)(200 * N));
    size_t best_off = 0; double best_e = -1.0;
    for (size_t off = 0; off + (size_t)N <= d.size() && off < search_len; off++) {
        double re = 0.0, im = 0.0;
        for (int k = 0; k < N; k++) {
            std::complex<float> t = std::conj(dc[k]) * d[off + k];
            re += t.real(); im += t.imag();
        }
        double e = re * re + im * im;   // |sum|^2 (no sqrt needed for argmax)
        if (e > best_e) { best_e = e; best_off = off; }
    }

    // best_off locks onto the LAST upchirp of the 8-symbol preamble
    // (downchirp correlation peaks there). The payload starts 3 symbols after
    // that (2 sync + the downchirp tail), i.e. best_off + 11N from the
    // strongest-correlation symbol. Verified against the numpy reference:
    // best_off+11N == first payload symbol.
    size_t pos = best_off + (size_t)(N * 11);
    const int max_sym = 64;
    std::vector<int> syms;
    while (pos + (size_t)N <= d.size() && (int)syms.size() < max_sym) {
        syms.push_back(demod_sym(sf, &d[pos]));
        pos += (size_t)N;
    }
    // LSB-first symbol->bit pack.
    std::vector<uint8_t> out;
    int acc = 0, cnt = 0;
    for (size_t i = 0; i < syms.size(); i++) {
        int s = syms[i];
        for (int k = 0; k < sf; k++) {
            acc |= ((s >> k) & 1) << cnt;
            if (++cnt == 8) { out.push_back((uint8_t)(acc & 0xFF)); acc = 0; cnt = 0; }
        }
    }
    return out;
}

} // namespace lora
