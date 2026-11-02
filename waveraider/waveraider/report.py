"""Render a survey to a standalone dark-themed HTML report.

Protocol-neutral: uses the plugin's columns/rows/detail plus the deterministic
reasoning backend, so Wi-Fi and BLE (and future protocols) all produce a report
with no per-protocol code here. Self-contained — inline CSS, no network.
"""
from __future__ import annotations

import html as _html

from .reason import get_backend

_SEV_COLOR = {"critical": "#f7768e", "warning": "#e0af68", "info": "#7aa2f7"}


def _esc(x) -> str:
    return _html.escape(str(x))


def render_html(plugin, survey) -> str:
    backend = get_backend("none", knowledge=plugin.knowledge, describe=plugin.describe)
    entities = plugin.list_entities(survey)
    cols = plugin.columns()

    sev_counts = {"critical": 0, "warning": 0, "info": 0}
    for f in survey.findings:
        sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1

    # header row
    thead = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    # entity rows, alerts cell tinted by the entity's worst finding
    body = []
    for e in entities:
        efind = [f for f in survey.findings if f.evidence.get("entity") == e.id]
        alerts = len(efind)
        worst = None
        for sev in ("critical", "warning", "info"):
            if any(f.severity == sev for f in efind):
                worst = sev
                break
        cells = plugin.row(e, alerts)
        tds = []
        for i, cell in enumerate(cells):
            style = ""
            if i == len(cells) - 1 and worst:
                style = f' style="color:{_SEV_COLOR[worst]};font-weight:600"'
            tds.append(f"<td{style}>{_esc(cell)}</td>")
        body.append(f"<tr>{''.join(tds)}</tr>")

    # findings + per-entity analysis
    analyses = []
    for e in entities:
        efind = [f for f in survey.findings if f.evidence.get("entity") == e.id]
        if not efind:
            continue
        text = backend.explain(survey, focus=e.id)
        analyses.append(
            f'<div class="card"><div class="cardhead">{_esc(e.label or e.id)}</div>'
            f'<pre>{_esc(text)}</pre></div>'
        )

    summary = " · ".join(
        f'<span style="color:{_SEV_COLOR[s]}">{sev_counts[s]} {s}</span>'
        for s in ("critical", "warning", "info") if sev_counts.get(s)
    ) or "no findings"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wave Raider — {_esc(plugin.name)}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0b0d10; color:#e8eaed;
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:28px 20px; }}
  h1 {{ margin:0 0 2px; font-size:26px; color:#7aa2f7; }}
  .sub {{ color:#6c757e; margin-bottom:20px; }}
  .summary {{ margin:14px 0 22px; font-size:15px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:26px; }}
  th,td {{ text-align:left; padding:8px 12px; border-bottom:1px solid #1c2128; }}
  th {{ color:#6c757e; font-size:12px; letter-spacing:1px; text-transform:uppercase; }}
  tr:hover td {{ background:#12161b; }}
  h2 {{ color:#a8b0b9; font-size:13px; letter-spacing:2px; text-transform:uppercase;
        border-bottom:1px solid #1c2128; padding-bottom:6px; }}
  .card {{ background:#14181d; border:1px solid #1c2128; border-radius:10px;
           padding:14px 16px; margin:12px 0; }}
  .cardhead {{ color:#7aa2f7; font-weight:600; margin-bottom:6px; }}
  pre {{ margin:0; white-space:pre-wrap; color:#cdd3da; }}
  a {{ color:#7aa2f7; }}
</style></head>
<body><div class="wrap">
  <h1>Wave Raider</h1>
  <div class="sub">{_esc(plugin.name)} · {_esc(survey.path)} · {survey.total_frames} frames · {len(entities)} entities</div>
  <div class="summary">Findings: {summary}</div>
  <table><thead><tr>{thead}</tr></thead><tbody>{''.join(body)}</tbody></table>
  <h2>Analysis</h2>
  {''.join(analyses) or '<div class="sub">No findings to analyze.</div>'}
  <div class="sub" style="margin-top:24px">Generated offline by Wave Raider · local-rules reasoning</div>
</div></body></html>"""
