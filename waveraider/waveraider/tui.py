"""Layer 4 — the real-time terminal UI.

Frames always flow through a background feeder thread into the stateful
Classifier; a periodic refresh repaints the table and panes on the UI thread.
A file source arrives in a blink, a replay source streams, live packet delivery remains incomplete.
Live Remote ID reception is unsupported; file/replay input is recorded data.
"""
from __future__ import annotations

import threading

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from .model import Survey
from .protocols import DEFAULT_PROTOCOL, get_plugin
from .reason import get_backend
from .sources import get_source

class WaveRaiderApp(App):
    TITLE = "Wave Raider"
    CSS = """
    #aps { width: 3fr; }
    #side { width: 2fr; }
    #detail { height: auto; border: round $primary; padding: 1; }
    #analysis { height: 1fr; border: round $accent; padding: 1; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, source_spec: str, protocol: str = DEFAULT_PROTOCOL) -> None:
        super().__init__()
        self.plugin = get_plugin(protocol)       # a retired or unknown name fails before the source opens
        self.source = get_source(source_spec)
        self.classifier = self.plugin.new_classifier()
        self.backend = get_backend(
            "none", knowledge=self.plugin.knowledge, describe=self.plugin.describe
        )
        self._lock = threading.Lock()
        self._done = False
        self._selected: str | None = None
        self._spec = source_spec

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="aps", cursor_type="row")
            with Vertical(id="side"):
                yield Static("", id="detail")
                yield Static("", id="analysis")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#aps", DataTable).add_columns(*self.plugin.columns())
        threading.Thread(target=self._feed, daemon=True).start()
        self.set_interval(0.3, self._refresh)

    # ---- ingest (background thread — never touches widgets) --------------

    def _feed(self) -> None:
        for pkt in self.source.frames():
            with self._lock:
                self.classifier.update(pkt)
        self._done = True

    # ---- refresh (UI thread) --------------------------------------------

    def _refresh(self) -> None:
        with self._lock:
            self.classifier.rebuild_findings()
            s = self.classifier.survey
            ents = self.plugin.list_entities(s)
            total = s.total_frames
            findings = list(s.findings)

        self.sub_title = (
            f"{self._spec}  ·  {total} frames  ·  "
            f"{'done' if self._done else 'reading source'}"
        )

        table = self.query_one("#aps", DataTable)
        table.clear()
        for e in ents:
            alerts = sum(1 for f in findings if f.evidence.get("entity") == e.id)
            table.add_row(*self.plugin.row(e, alerts), key=e.id)
        # keep the cursor on the same entity across repaints
        keys = [e.id for e in ents]
        if self._selected not in keys:
            self._selected = keys[0] if keys else None
        if self._selected is not None:
            try:
                table.move_cursor(row=keys.index(self._selected))
            except Exception:
                pass
            self._render_panes(self._selected)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self._selected = event.row_key.value
            self._render_panes(self._selected)

    def _render_panes(self, ent_id: str) -> None:
        with self._lock:
            s = self.classifier.survey
            e = s.entities.get(ent_id)
            detail = self.plugin.detail(s, e) if e is not None else ""
            analysis = self.backend.explain(s, focus=ent_id)
        self.query_one("#detail", Static).update(detail)
        self.query_one("#analysis", Static).update(
            f"ANALYSIS  ({self.backend.name})\n\n{analysis}"
        )


def run(source_spec: str, protocol: str = DEFAULT_PROTOCOL) -> None:
    WaveRaiderApp(source_spec, protocol=protocol).run()
