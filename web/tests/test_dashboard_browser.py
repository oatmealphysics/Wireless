"""Offline browser-level tests for the Flask dashboard.

The suite serves the real application on an ephemeral loopback port and drives
the installed Google Chrome channel through Playwright. All application state
is forced into simulation/dry-run mode. Dashboard-page HTTP(S) requests to
anything other than a loopback address are aborted and fail the test.
"""

from __future__ import annotations

import copy
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server


REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
APP_MODULE_NAME = "dashboard_web_app_browser_tests"
BROWSER_CHANNEL = os.environ.get("DASHBOARD_BROWSER_CHANNEL", "chrome")


def _load_dashboard_module():
    """Load web/app.py under a test-only module name."""
    spec = importlib.util.spec_from_file_location(
        APP_MODULE_NAME, WEB_DIR / "app.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load web/app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[APP_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


web_app = _load_dashboard_module()


class _QuietRequestHandler(WSGIRequestHandler):
    """Keep successful browser-test requests out of unittest output."""

    def log_request(self, code="-", size="-"):
        pass


class DashboardBrowserTests(unittest.TestCase):
    """Exercise the dashboard as a browser while remaining fully offline."""

    @classmethod
    def setUpClass(cls):
        web_app.app.config.update(TESTING=True)
        web_app.simulate_mode = True
        web_app.dry_run_mode = True

        cls.httpd = make_server(
            "127.0.0.1",
            0,
            web_app.app,
            threaded=True,
            request_handler=_QuietRequestHandler,
        )
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.server_thread = threading.Thread(
            target=cls.httpd.serve_forever,
            name="dashboard-browser-test-server",
            daemon=True,
        )
        cls.server_thread.start()

        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(
                channel=BROWSER_CHANNEL,
                headless=True,
                args=[
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--no-default-browser-check",
                ],
            )
        except Exception:
            cls.httpd.shutdown()
            cls.httpd.server_close()
            cls.server_thread.join(timeout=5)
            playwright = getattr(cls, "playwright", None)
            if playwright is not None:
                playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            try:
                cls.playwright.stop()
            finally:
                cls.httpd.shutdown()
                cls.httpd.server_close()
                cls.server_thread.join(timeout=5)

    def setUp(self):
        self.assertFalse(
            web_app.is_scanning,
            "A scan worker leaked from the previous browser test",
        )
        web_app.simulate_mode = True
        web_app.dry_run_mode = True
        with web_app.scan_lock:
            web_app.targets.clear()
            web_app.action_log.clear()
            web_app.scanner_status.clear()
            web_app.scanner_status.update(
                {name: "idle" for name in web_app.DEFAULT_PROTOCOLS}
            )
            web_app.is_scanning = False
        with web_app.events_lock:
            web_app.events.clear()
            web_app.next_event_id = 0

        # Turn the safety properties into executable invariants. If a browser
        # flow ever reaches a live backend or hardware probe, the test fails.
        self.guard_live_scan = patch.object(
            web_app,
            "run_scan_live",
            side_effect=AssertionError("browser test attempted a live scan"),
        )
        self.guard_live_action = patch.object(
            web_app,
            "execute_action_live",
            side_effect=AssertionError("browser test attempted a live action"),
        )
        self.guard_hardware = patch.object(
            web_app,
            "detect_hardware",
            return_value={
                "wifi": {"available": True, "interfaces": ["wlan0"], "default": "wlan0"},
                "ble": {"available": False, "interfaces": [], "error": "No BLE stack found"},
                "sdr": {"available": False, "devices": [], "error": "SoapySDR not installed"},
                "mavlink": {"available": True, "bindable_ports": [14550], "note": "UDP listener"},
            },
        )
        self.guard_live_scan.start()
        self.addCleanup(self.guard_live_scan.stop)
        self.guard_live_action.start()
        self.addCleanup(self.guard_live_action.stop)
        self.guard_hardware.start()
        self.addCleanup(self.guard_hardware.stop)

        self.non_loopback_requests = []
        self.page_errors = []
        self.console_errors = []
        self.fail_scan_posts = False

        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800},
        )
        self.addCleanup(self.context.close)
        self.context.route("**/*", self._route_request)
        self.page = self.context.new_page()
        self.addCleanup(self.page.close)
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on(
            "console",
            lambda message: self.console_errors.append(message.text)
            if message.type == "error"
            else None,
        )

    def tearDown(self):
        self.assertEqual(
            self.non_loopback_requests,
            [],
            "Browser attempted non-loopback HTTP(S) requests",
        )
        self.assertEqual(self.page_errors, [], "Browser raised JavaScript errors")
        self.assertTrue(web_app.simulate_mode)
        self.assertTrue(web_app.dry_run_mode)

    @staticmethod
    def _is_loopback_hostname(hostname):
        if not hostname:
            return False
        if hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return False

    def _route_request(self, route, request):
        parsed = urlsplit(request.url)
        if parsed.scheme in {"http", "https"}:
            if not self._is_loopback_hostname(parsed.hostname):
                self.non_loopback_requests.append(request.url)
                route.abort("blockedbyclient")
                return
            if (
                self.fail_scan_posts
                and request.method == "POST"
                and parsed.path == "/api/scan"
            ):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"error": "simulated scan service failure"}),
                )
                return
        route.continue_()

    def _open_dashboard(self):
        response = self.page.goto(self.base_url, wait_until="domcontentloaded")
        self.assertIsNotNone(response)
        self.assertTrue(response.ok, f"Dashboard returned HTTP {response.status}")
        expect(self.page.locator("#mode-badge")).to_have_text("SIMULATE MODE")

    def _seed_simulated_target(self):
        target = copy.deepcopy(web_app.SIMULATED_TARGETS[0])
        now = time.time()
        target.update(
            first_seen=now,
            last_seen=now,
            score=web_app._score_target(target),
        )
        target["threat_level"] = web_app._threat_from_score(target)
        with web_app.scan_lock:
            web_app.targets[target["id"]] = target
        return target

    def test_boots_without_page_or_console_errors(self):
        self._open_dashboard()

        expect(self.page).to_have_title("Drone Scanner")
        expect(self.page.locator("#btn-scan")).to_be_enabled()
        expect(self.page.locator("#target-count")).to_have_text("0 targets")
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_complete_simulated_scan_action_log_and_clear(self):
        self._open_dashboard()

        scan_button = self.page.locator("#btn-scan")
        scan_button.click()
        expect(scan_button).to_be_disabled()
        expect(self.page.locator("#scan-overlay")).to_be_visible()

        target_items = self.page.locator("#target-items .target-item")
        expect(target_items).to_have_count(
            len(web_app.SIMULATED_TARGETS), timeout=15_000,
        )
        expect(scan_button).to_be_enabled(timeout=15_000)
        expect(self.page.locator("#scan-overlay")).to_be_hidden()

        tello = self.page.locator(
            '#target-items .target-item[data-target-id="wifi:60:60:1F:AA:BB:CC"]'
        )
        tello.click()
        expect(self.page.locator(".detail-title")).to_have_text("TELLO-AABBCC")

        action = self.page.locator('.action-card[data-action-name="streamon"]')
        action.click()
        expect(self.page.locator("#action-result")).to_contain_text("[dry_run]")
        expect(self.page.locator("#action-result")).to_contain_text("[DRY-RUN]")
        expect(self.page.locator("#action-log-entries .log-entry")).to_have_count(1)
        expect(self.page.locator("#action-log-entries")).to_contain_text("Capture Video")
        expect(self.page.locator("#action-log-entries")).to_contain_text("[dry_run]")

        self.assertEqual(len(web_app.action_log), 1)
        self.assertEqual(web_app.action_log[0]["status"], "dry_run")

        self.page.locator("#btn-clear").click()
        expect(target_items).to_have_count(0)
        expect(self.page.locator("#target-count")).to_have_text("0 targets")
        expect(self.page.get_by_text("No targets found. Click Scan to begin.")).to_be_visible()
        expect(self.page.locator("#detail-empty")).to_be_visible()
        self.assertEqual(web_app.targets, {})
        self.assertEqual(self.console_errors, [])

    def test_keyboard_focus_and_target_selection(self):
        target = self._seed_simulated_target()
        second_target = copy.deepcopy(web_app.SIMULATED_TARGETS[1])
        now = time.time()
        second_target.update(
            first_seen=now,
            last_seen=now,
            score=web_app._score_target(second_target),
        )
        second_target["threat_level"] = web_app._threat_from_score(second_target)
        with web_app.scan_lock:
            web_app.targets[second_target["id"]] = second_target
        self._open_dashboard()

        target_buttons = self.page.locator("#target-items .target-item")
        expect(target_buttons).to_have_count(2)
        target_button = target_buttons.first
        self.assertEqual(target_button.get_attribute("type"), "button")
        target_button.focus()
        self.assertTrue(
            target_button.evaluate("element => document.activeElement === element")
        )
        target_button.press("Enter")

        expect(self.page.locator(".detail-title")).to_have_text(target["name"])
        expect(target_button).to_have_attribute("aria-pressed", "true")
        self.assertEqual(
            self.page.evaluate("document.activeElement?.dataset?.targetId"),
            target["id"],
        )

        target_button.press("ArrowDown")
        expect(self.page.locator(".detail-title")).to_have_text(
            second_target["name"],
        )
        self.assertEqual(
            self.page.evaluate("document.activeElement?.dataset?.targetId"),
            second_target["id"],
        )
        expect(target_buttons.nth(1)).to_have_attribute("aria-current", "true")

        action_button = self.page.locator(".action-card").first
        action_button.focus()
        self.assertTrue(
            action_button.evaluate("element => document.activeElement === element")
        )
        self.assertEqual(action_button.get_attribute("type"), "button")
        self.assertEqual(self.console_errors, [])

    def test_out_of_band_clear_moves_focus_to_scan(self):
        self._seed_simulated_target()
        self._open_dashboard()
        self.page.wait_for_function(
            "eventSource && eventSource.readyState === EventSource.OPEN"
        )

        target_button = self.page.locator("#target-items .target-item")
        target_button.focus()
        target_button.press("Enter")
        self.assertEqual(
            self.page.evaluate("document.activeElement?.dataset?.targetId"),
            web_app.SIMULATED_TARGETS[0]["id"],
        )

        clear_request = Request(f"{self.base_url}/api/clear", method="POST")
        with urlopen(clear_request, timeout=5) as response:
            self.assertEqual(response.status, 200)

        expect(target_button).to_have_count(0)
        expect(self.page.locator("#target-count")).to_have_text("0 targets")
        self.assertEqual(
            self.page.evaluate("document.activeElement?.id"),
            "btn-scan",
        )

    def test_scan_post_failure_restores_controls(self):
        self.fail_scan_posts = True
        self._open_dashboard()

        scan_button = self.page.locator("#btn-scan")
        scan_button.click()

        expect(self.page.locator("#scan-overlay")).to_be_hidden()
        expect(scan_button).to_be_enabled()
        expect(self.page.locator(".event-toast.error")).to_contain_text(
            "Unable to start scan: simulated scan service failure"
        )
        self.assertFalse(web_app.is_scanning)
        self.assertEqual(web_app.targets, {})

    def test_390px_viewport_has_no_horizontal_overflow(self):
        self._seed_simulated_target()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._open_dashboard()
        self.page.locator("#target-items .target-item").press("Enter")
        expect(self.page.locator(".detail-title")).to_be_visible()

        dimensions = self.page.evaluate(
            """() => ({
                viewport: document.documentElement.clientWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
            })"""
        )
        self.assertEqual(dimensions["viewport"], 390)
        self.assertLessEqual(dimensions["documentWidth"], dimensions["viewport"])
        self.assertLessEqual(dimensions["bodyWidth"], dimensions["viewport"])

        viewport_height = self.page.evaluate("window.innerHeight")
        body_height = self.page.evaluate("document.body.clientHeight")
        self.assertEqual(body_height, viewport_height)

        detail_panel = self.page.locator("#detail-panel")
        scroll_metrics = detail_panel.evaluate(
            "element => ({client: element.clientHeight, scroll: element.scrollHeight})"
        )
        self.assertGreater(scroll_metrics["scroll"], scroll_metrics["client"])
        action_log = self.page.locator(".log-panel")
        action_log.scroll_into_view_if_needed()
        expect(action_log).to_be_in_viewport()
        self.assertGreater(detail_panel.evaluate("element => element.scrollTop"), 0)
        self.assertEqual(self.console_errors, [])


    def test_log_viewer_toggle_and_entries(self):
        self._open_dashboard()

        log_viewer = self.page.locator("#log-viewer")
        expect(log_viewer).to_have_class(re.compile(r"collapsed"))
        expect(self.page.locator("#log-viewer-body")).to_be_hidden()

        self.page.locator("#log-viewer-header").click()
        expect(log_viewer).to_have_class(re.compile(r"expanded"))
        expect(self.page.locator("#log-viewer-body")).to_be_visible()
        expect(self.page.locator("#log-viewer-count")).not_to_have_text("0")
        expect(self.page.locator(".log-line")).to_have_count(
            self.page.locator(".log-line").count(), timeout=2000,
        )

        self.page.locator("#log-viewer-header").click()
        expect(log_viewer).to_have_class(re.compile(r"collapsed"))
        self.assertEqual(self.console_errors, [])

    def test_log_viewer_clear(self):
        self._open_dashboard()
        self.page.locator("#log-viewer-header").click()
        expect(self.page.locator("#log-viewer-body")).to_be_visible()
        initial_count = int(self.page.locator("#log-viewer-count").text_content())
        self.assertGreater(initial_count, 0)

        self.page.locator("#log-clear-btn").click()
        expect(self.page.locator("#log-viewer-count")).to_have_text("0")
        expect(self.page.locator(".log-line")).to_have_count(0)
        self.assertEqual(self.console_errors, [])

    def test_hardware_widget_popover(self):
        self._open_dashboard()

        hw_widget = self.page.locator("#hw-widget")
        expect(hw_widget).to_be_visible()
        expect(hw_widget).to_have_attribute("aria-expanded", "false")

        popover = self.page.locator("#hw-popover")
        expect(popover).to_be_hidden()

        hw_widget.click()
        expect(popover).to_be_visible()
        expect(hw_widget).to_have_attribute("aria-expanded", "true")
        expect(popover).to_contain_text("WiFi")
        expect(popover).to_contain_text("MAVLink UDP")

        self.page.locator("body").click(position={"x": 10, "y": 10})
        expect(popover).to_be_hidden()
        expect(hw_widget).to_have_attribute("aria-expanded", "false")
        self.assertEqual(self.console_errors, [])

    def test_hardware_indicators_reflect_status(self):
        self._open_dashboard()

        self.page.wait_for_timeout(500)
        wifi_dot = self.page.locator("#hw-wifi-indicator .hw-dot")
        ble_dot = self.page.locator("#hw-ble-indicator .hw-dot")
        sdr_dot = self.page.locator("#hw-sdr-indicator .hw-dot")
        mav_dot = self.page.locator("#hw-mav-indicator .hw-dot")

        expect(wifi_dot).to_have_class(re.compile(r"available"))
        expect(ble_dot).to_have_class(re.compile(r"unavailable"))
        expect(sdr_dot).to_have_class(re.compile(r"unavailable"))
        expect(mav_dot).to_have_class(re.compile(r"available"))
        self.assertEqual(self.console_errors, [])

    def test_scan_events_appear_in_log_viewer(self):
        self._open_dashboard()

        self.page.locator("#log-viewer-header").click()
        expect(self.page.locator("#log-viewer-body")).to_be_visible()

        self.page.locator("#log-clear-btn").click()
        expect(self.page.locator("#log-viewer-count")).to_have_text("0")

        scan_button = self.page.locator("#btn-scan")
        scan_button.click()
        expect(scan_button).to_be_enabled(timeout=15_000)

        log_count = int(self.page.locator("#log-viewer-count").text_content())
        self.assertGreater(log_count, 0)
        expect(self.page.locator("#log-viewer-body")).to_contain_text("scan", ignore_case=True)
        self.assertEqual(self.console_errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
