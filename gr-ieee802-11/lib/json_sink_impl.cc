/*
 * Copyright (C) 2026 elrs-sdr integration
 *
 * json_sink — message block that turns each decoded 802.11 frame (from
 * ieee802_11_decode_mac's 'out' message port) into one JSON line on stdout.
 *
 * License: GPL-3.0 (same as the rest of gr-ieee802-11).
 *
 * 802.11 frame header layout (starts at the frame-control field):
 *   off  len  field
 *   0    2    frame control
 *   2    2    duration
 *   4    6    address 1 (DA / RA)
 *   10   6    address 2 (SA / TA)
 *   16   6    address 3 (BSSID for mgmt, TA for data)
 *   22   2    sequence control
 *   [24   4    address 4 (only when ToDS|FromDS both set)]
 *   then optional 2-byte QoS control, then body.
 *
 * For a Beacon (mgmt/subtype=8) the body begins with the SSID element:
 *   0x00  <len>  <ssid bytes>
 */
#include <ieee802_11/json_sink.h>
#include "json_sink_impl.h"

#include <gnuradio/io_signature.h>
#include <boost/make_shared.hpp>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>

using namespace gr::ieee802_11;

static std::string mac_str(const uint8_t* m)
{
    char buf[32];
    snprintf(buf, sizeof(buf), "%02x:%02x:%02x:%02x:%02x:%02x",
             m[0], m[1], m[2], m[3], m[4], m[5]);
    return std::string(buf);
}

static std::string json_escaped(const uint8_t* s, int n)
{
    std::string out;
    out.reserve(n);
    for (int i = 0; i < n; i++) {
        uint8_t c = s[i];
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c >= 0x20 && c < 0x7f)
                out += (char)c;
            // else: drop non-printable (keep the JSON clean)
        }
    }
    return out;
}

json_sink_impl::json_sink_impl(double center_freq_hz, int channel, bool include_raw)
    : block("json_sink",
            gr::io_signature::make(0, 0, 0),
            gr::io_signature::make(0, 0, 0)),
      d_freq_hz(center_freq_hz),
      d_channel(channel),
      d_include_raw(include_raw)
{
    message_port_register_in(pmt::mp("in"));
    set_msg_handler(pmt::mp("in"),
                    [this](pmt::pmt_t msg) { this->handle_frame(msg); });
}

void json_sink_impl::handle_frame(pmt::pmt_t msg)
{
    // decode_mac publishes cons(meta_dict, payload_blob)
    if (!pmt::is_pair(msg))
        return;
    pmt::pmt_t meta = pmt::car(msg);
    pmt::pmt_t payload = pmt::cdr(msg);
    if (!pmt::is_blob(payload))
        return;

    const uint8_t* f = (const uint8_t*)pmt::blob_data(payload);
    long len = pmt::blob_size(payload);
    if (len < 24)               // minimum: fc+duration+3 addresses
        return;

    // ---- parse the fixed header ----
    uint16_t fc = f[0] | (f[1] << 8);
    int type = (fc >> 2) & 0x3;
    int subtype = (fc >> 4) & 0xf;
    bool to_ds = fc & 0x1;
    bool from_ds = fc & 0x2;
    bool mgmt = (type == 0);

    const uint8_t* da = f + 4;
    const uint8_t* sa = f + 10;
    const uint8_t* a3 = f + 16;

    // BSSID = address3 for management and 2-address (ad-hoc) frames;
    // for data frames the BSSID is in address1 (RA).
    std::string bssid = mac_str(mgmt ? a3 : da);
    std::string src = mac_str(sa);
    std::string dst = mac_str(da);

    // ---- find the SSID element (only meaningful for beacons, but cheap) ----
    std::string ssid;
    int body = 24 + (to_ds && from_ds ? 4 : 0);   // after seq ctl (+ addr4)
    // skip a 2-byte QoS control for QoS data frames
    bool qos = (fc >> 12) & 0x1;
    if (qos && !mgmt)
        body += 2;
    // scan information elements for the SSID element (id 0)
    for (int p = body; p + 2 <= len; ) {
        int eid = f[p];
        int elen = f[p + 1];
        if (p + 2 + elen > len)
            break;
        if (eid == 0 && elen > 0 && elen <= 32) {
            ssid = json_escaped(f + p + 2, elen);
            break;
        }
        p += 2 + elen;
    }

    // ---- emit one JSON line (stdout is line-buffered via flush) ----
    double ts = std::chrono::duration<double>(
                    std::chrono::system_clock::now().time_since_epoch())
                    .count();
    std::string line = "{\"ts\":";
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%.6f", ts);
    line += tmp;
    line += ",\"ch\":";
    snprintf(tmp, sizeof(tmp), "%d", d_channel);
    line += tmp;
    line += ",\"freq_mhz\":";
    snprintf(tmp, sizeof(tmp), "%.1f", d_freq_hz / 1e6);
    line += tmp;
    line += ",\"fc\":";
    snprintf(tmp, sizeof(tmp), "%d", fc);
    line += tmp;
    line += ",\"type\":";
    line += std::to_string(type);
    line += ",\"subtype\":";
    line += std::to_string(subtype);
    line += ",\"bssid\":\"" + bssid + "\"";
    line += ",\"src\":\"" + src + "\"";
    line += ",\"dst\":\"" + dst + "\"";
    line += ",\"ssid\":\"" + ssid + "\"";
    line += "}";

    printf("%s\n", line.c_str());
    fflush(stdout);
}

json_sink::sptr json_sink::make(double center_freq_hz, int channel, bool include_raw)
{
    return gnuradio::get_initial_sptr(
        new json_sink_impl(center_freq_hz, channel, include_raw));
}
