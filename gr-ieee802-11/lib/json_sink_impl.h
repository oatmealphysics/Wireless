/*
 * json_sink_impl — see json_sink.h. Receives decoded 802.11 frames on the
 * 'in' message port, parses the header (addresses, type/subtype, SSID) and
 * prints one JSON line per frame to stdout. No stream I/O (0 in / 0 out).
 */
#ifndef INCLUDED_IEEE802_11_JSON_SINK_IMPL_H
#define INCLUDED_IEEE802_11_JSON_SINK_IMPL_H

#include <ieee802_11/json_sink.h>

namespace gr {
namespace ieee802_11 {

class json_sink_impl : public json_sink
{
public:
    json_sink_impl(double center_freq_hz, int channel, bool include_raw);

private:
    void handle_frame(pmt::pmt_t msg);

    double d_freq_hz;
    int d_channel;
    bool d_include_raw;
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_JSON_SINK_IMPL_H */
