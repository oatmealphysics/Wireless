"""Headless check that the TUI constructs with its Remote ID default, renders the
transmitter table, and repaints from a recorded capture — no human, no live terminal.
Run:
    .venv/bin/python tools/tui_smoke.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from waveraider.protocols import get_plugin
from waveraider.tui import WaveRaiderApp
from waveraider.uas_classify import survey

SAMPLE = "data/uas_sample.pcap"


async def _run() -> None:
    plugin = get_plugin()
    expected = len(plugin.list_entities(survey(SAMPLE)))
    app = WaveRaiderApp("file:" + SAMPLE)             # no protocol given: Remote ID
    assert app.plugin.name == "uas", app.plugin.name
    async with app.run_test() as pilot:
        await pilot.pause(1.0)                         # let the feeder + refresh settle
        table = app.query_one("#aps")
        assert table.row_count == expected, f"expected {expected} transmitter rows, got {table.row_count}"
        svg = app.export_screenshot()
        assert plugin.columns()[0] in svg, "table/panes did not render"
    print(f"TUI smoke OK: {expected} Remote ID transmitter rows, live refresh renders")


if __name__ == "__main__":
    asyncio.run(_run())
