"""Static safety boundary for the optional native UHD receive helper."""

import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "uhd-elrs-sync-rx.c")


class UhdReceiveSourceTests(unittest.TestCase):
    def test_helper_has_no_transmit_api(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("uhd-elrs-receive-anchor-v1", text)
        self.assertIn("uhd_rx_streamer_recv", text)
        self.assertNotIn("uhd_tx_streamer", text)
        self.assertNotIn("uhd_usrp_set_tx", text)
        self.assertNotIn("uhd_usrp_get_tx_stream", text)

    def test_helper_is_bounded_and_non_overwriting(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("duration > 5.0", text)
        self.assertIn("path_exists(argv[1])", text)
        self.assertIn("path_exists(argv[2])", text)
        self.assertIn('\\"receive_only\\": true', text)
        self.assertIn('\\"transmit_capable\\": false', text)


if __name__ == "__main__":
    unittest.main()
