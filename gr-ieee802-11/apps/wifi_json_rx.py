#!/usr/bin/env python3
#
# wifi_json_rx.py — headless 802.11 a/g RX demod (gr-ieee802-11) that prints
# one JSON line per decoded frame to stdout via the json_sink block.
#
# Signal chain (exactly as examples/wifi_rx.grc):
#   usrp -> complex_to_mag_squared -> moving_average(2) --+
#         usrp -> delay(1) -> conjugate -> multiply <-complex_to_mag-> |
#                usrp -> delay(1) -> sync_short(in0)                  |
#   moving_average(2) -> complex_to_mag -> moving_average(2) -> divide(0)
#   divide(1) <- moving_average(1)
#   usrp -> multiply(0)
#   multiply -> moving_average(1)
#   divide -> sync_short(in2) ; delay -> sync_short(in1) ; delay(1) -> sync_short(in0)
#   sync_short -> delay(1) -> sync_long(in1) ; sync_short -> sync_long(in0)
#   sync_long -> stream_to_vector(64) -> fft_vcc(64) -> frame_equalizer
#   frame_equalizer -> decode_mac -> (message) json_sink -> stdout
#
# Runs unattended (no qtgui). The elrs-sdr webui spawns this and reads stdout.
#
import argparse
import sys

import gnuradio as gr
import uhd
import ieee802_11


def build_graph(freq_hz, srate, gain_db, channel, device="type=b200"):
    tb = gr.top_block()

    # ---- UHD source (one RX channel, B205) ----
    usrp = uhd.usrp.MultiUSRP(device)
    usrp.set_rx_freq(freq_hz, 0)
    usrp.set_rx_rate(srate, 0)
    usrp.set_rx_gain(gain_db, 0)
    try:
        usrp.set_rx_antenna("RX2", 0)
    except Exception:
        pass

    # ---- demod chain (signatures verified against the block headers) ----
    sync_short = ieee802_11.sync_short(0.8, 2, False, False)
    sync_long = ieee802_11.sync_long(240, False, False)
    fft = gr.fft_vcc(64, False, "", 1, 1)
    eq = ieee802_11.frame_equalizer(ieee802_11.LS, float(freq_hz), float(srate),
                                    False, False)
    decode = ieee802_11.decode_mac(True, False)
    sink = ieee802_11.json_sink(float(freq_hz), int(channel), False)

    # ---- preamble-energy helpers (mirror the GRC wiring) ----
    c2magsq = gr.blocks_complex_to_mag_squared(1)
    c2mag = gr.blocks_complex_to_mag(1)
    ma1 = gr.blocks_moving_average_ff(2, 1, 1)   # signal-level MA
    ma2 = gr.blocks_moving_average_ff(2, 1, 1)   # noise-floor MA
    delay = gr.blocks_delay(1)
    delay0 = gr.blocks_delay(1)
    conj = gr.blocks_conjugate_cc()
    mul = gr.blocks_multiply_cc(1)
    divide = gr.blocks_divide_cc(1)
    s2v = gr.blocks_stream_to_vector(gr.sizeof_gr_complex, 64)

    # usrp fan-out
    tb.connect(usrp, c2magsq)          # power
    tb.connect(usrp, delay0)           # delayed sample
    tb.connect(usrp, mul, 0)           # current sample into multiply

    # power path: mag^2 -> MA1 ; also MA1 -> c2mag -> MA2 -> divide
    tb.connect(c2magsq, ma1)
    tb.connect(ma1, c2mag)
    tb.connect(c2mag, ma2)
    tb.connect(ma2, divide, 0)

    # conjugate * delayed  -> feeds MA1 (adaptive level)
    tb.connect(delay0, conj)
    tb.connect(conj, mul, 1)
    tb.connect(mul, ma1)

    # divide(1) from MA1
    tb.connect(ma1, divide, 1)

    # sync_short: in0=delay0, in1=delay (post-short), in2=divide (threshold)
    tb.connect(delay0, sync_short, 0)
    tb.connect(delay, sync_short, 1)
    tb.connect(divide, sync_short, 2)

    # short -> long
    tb.connect(sync_short, delay)
    tb.connect(delay, sync_long, 1)
    tb.connect(sync_short, sync_long, 0)

    # long -> fft -> equalizer -> decode -> json sink (message)
    tb.connect(sync_long, s2v)
    tb.connect(s2v, fft)
    tb.connect(fft, eq)
    tb.connect(eq, decode)
    tb.msg_connect(decode, "out", sink, "in")

    return tb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", type=float, default=2437e6)
    ap.add_argument("--srate", type=float, default=20e6)
    ap.add_argument("--gain", type=float, default=40.0)
    ap.add_argument("--channel", type=int, default=6)
    ap.add_argument("--device", default="type=b200")
    args = ap.parse_args()

    # stdout is the JSON channel; all other logging goes to stderr
    sys.stderr.write(
        f"[wifi_json_rx] freq={args.freq/1e6:.1f} MHz ch={args.channel} "
        f"srate={args.srate/1e6:.1f} Msps gain={args.gain:.0f} dB\n")

    tb = build_graph(args.freq, args.srate, args.gain, args.channel,
                     args.device)
    try:
        tb.start()
        tb.wait()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            tb.stop()
            tb.wait()
        except Exception:
            pass


if __name__ == "__main__":
    main()
