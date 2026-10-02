/*
 * json_sink — tap the decoded 802.11 MAC frames from ieee802_11_decode_mac
 * (a message PDU: payload = the 802.11 frame starting at the frame-control
 * field, + a meta dict) and print one JSON line per frame to stdout:
 *   {"ts":..., "ch":..., "bssid":"..","src":"..","dst":"..",
 *    "ssid":"..","type":..,"subtype":..,"fc":..}
 *
 * This is the webui integration seam: the elrs-sdr webui spawns the gr
 * ieee802_11 flowgraph with this block wired to decode_mac's 'out' port and
 * reads these JSON lines to populate the detections table (MAC column).
 *
 * The 802.11 header (IEEE 802.11-2016 8.1) begins with:
 *   2  frame control
 *   2  duration
 *   6  address1 (DA)
 *   6  address2 (SA)
 *   6  address3 (BSSID for management / TA for data)
 *   2  sequence control
 *   [4  address4 (for 4-address frames)]
 *   2  QoS control (if QoS)
 *   ...
 *   then body (for a Beacon: SSID element = 0x00 len "ssid")
 */
#ifndef INCLUDED_IEEE802_11_JSON_SINK_H
#define INCLUDED_IEEE802_11_JSON_SINK_H

#include <gnuradio/block.h>
#include <ieee802_11/api.h>

namespace gr {
namespace ieee802_11 {

class IEEE802_11_API json_sink : virtual public block
{
public:
    typedef std::shared_ptr<json_sink> sptr;
    static sptr make(double center_freq_hz, int channel, bool include_raw);
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_JSON_SINK_H */
