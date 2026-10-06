"""Quiet Precision is the only design: one token set, no old palette, no dead or overridden
rules, and the states and layout that behavior depends on are still styled."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from waveraider import web

PKG = Path(web.__file__).parent
PAGES = {name: re.findall(r"<style>(.*?)</style>", (PKG / name).read_text(), re.S) for name in ("monitor.html", "settings.html")}
SHEETS = {"monitor.html": PAGES["monitor.html"][0], "settings.html": PAGES["settings.html"][0],
          "inspect_rf.css": (PKG / "inspect_rf.css").read_text()}
OLD_PALETTE = ("#0b0d10", "#0f1318", "#14181d", "#1c2128", "#2a3038", "#e8eaed", "#aeb8c4", "#8a939d",
               "#6c757e", "#7aa2f7", "#9ece6a", "#e0af68", "#f7768e", "#182030", "#bb9af7", "#12161b", "#1a2230")
RETIRED_DASHBOARD_CLASSES = (".nv", ".badge", ".scard", ".fcard", ".ptag", ".spacer", ".rtitle", ".ikind", ".tab.off", ".vlbl")
# Deliberate non-token colors: banner tints, primary hover shade, and the drawer shadow.
ALLOWED_LITERALS = {"#1d1810", "#241218", "#a1bbd0", "#0003"}


def _css(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _tokens(css):
    roots = re.findall(r"(?:^|\})\s*:root\s*\{([^}]*)\}", _css(css))
    assert len(roots) == 1, "exactly one :root token block"
    return dict(re.findall(r"(--[a-z0-9]+)\s*:\s*([^;]+)", roots[0]))


def _rules(css):
    """(media condition or None, selector list, [(property, important)]) in source order."""
    out, pos, css = [], 0, _css(css)
    for m in re.finditer(r"@media([^{]*)\{((?:[^{}]*\{[^{}]*\})*)\s*\}|([^{}@]+)\{([^{}]*)\}", css):
        if m.group(1) is not None:
            cond = re.sub(r"\s+", "", m.group(1))
            for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", m.group(2)):
                out.append((cond, sel, body))
        else:
            out.append((None, m.group(3), m.group(4)))
    rules = []
    for cond, sel, body in out:
        sels = [re.sub(r"\s+", " ", s.strip()) for s in sel.split(",")]
        decls = [(d.split(":", 1)[0].strip().lower(), "!important" in d) for d in body.split(";") if ":" in d]
        rules.append((cond, sels, decls))
    return rules


def test_one_shared_token_set():
    monitor, settings = _tokens(SHEETS["monitor.html"]), _tokens(SHEETS["settings.html"])
    assert monitor == settings and "--accent" in monitor and "--bg" in monitor
    assert ":root" not in _css(SHEETS["inspect_rf.css"])                 # the drawer uses Monitor's tokens


@pytest.mark.parametrize("name", SHEETS)
def test_no_old_palette_or_retired_dashboard_selectors(name):
    css = _css(SHEETS[name]).lower()
    assert [c for c in OLD_PALETTE if c in css] == []
    selectors = {s for _, sels, _ in _rules(SHEETS[name]) for s in sels}
    assert [c for c in RETIRED_DASHBOARD_CLASSES if any(re.search(re.escape(c) + r"(?![\w-])", s) for s in selectors)] == []


@pytest.mark.parametrize("name", SHEETS)
def test_colors_use_tokens(name):
    token_values = {v.strip().lower() for v in _tokens(SHEETS["monitor.html"]).values()}
    body = re.sub(r":root\s*\{[^}]*\}", "", _css(SHEETS[name]))
    literals = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", body)}
    assert literals & token_values == set()                             # a token-equal color must use the token
    assert literals <= ALLOWED_LITERALS


@pytest.mark.parametrize("name", SHEETS)
def test_no_declaration_is_overridden_by_a_later_rule_with_the_same_selector(name):
    rules = _rules(SHEETS[name])
    dead = []
    for i, (cond, sels, decls) in enumerate(rules):
        for prop, imp in decls:
            if all(any(s in sels2 and cond2 in (cond, None) and any(p2 == prop and (imp2 or not imp) for p2, imp2 in decls2)
                       for cond2, sels2, decls2 in rules[i + 1:]) for s in sels):
                dead.append((sels, prop))
    assert dead == []


def test_behavior_critical_states_and_layout_are_styled():
    monitor, settings, drawer = (_css(SHEETS[n]).replace(" ", "") for n in ("monitor.html", "settings.html", "inspect_rf.css"))
    assert "[hidden]{display:none!important}" in monitor
    for page in (monitor, settings):
        assert re.search(r":focus-visible[^{]*\{[^}]*outline:", page)
        assert re.search(r"\.tab\.on[^{]*\{[^}]*box-shadow:inset", page)          # selected navigation marker
        assert re.search(r"\.btn\[disabled\][^{]*\{[^}]*cursor:not-allowed", page)
        assert "@media(max-width:760px)" in page and "@media(prefers-reduced-motion:reduce)" in page
        assert re.search(r"body\{[^}]*font:16px/1\.5system-ui", page)
    assert re.search(r"\.irf-drawer\{[^}]*position:fixed", monitor)
    assert re.search(r"\.irf-body\{[^}]*overflow:auto", monitor)
    assert re.search(r"tr\.sel td[^{]*\{[^}]*background:var\(--sel\)".replace(" ", ""), monitor)
    assert all(line.startswith(".irf ") for line in _css(SHEETS["inspect_rf.css"]).splitlines() if "{" in line)
    assert "var(--" in drawer


def _contrast(a, b):
    def luminance(h):
        rgb = [int(h[i:i+2], 16) / 255 for i in (1, 3, 5)]
        rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))
    lo, hi = sorted((luminance(a), luminance(b)))
    return (hi + .05) / (lo + .05)


@pytest.mark.parametrize('prefix', ['', 'light'])
def test_theme_contrast_for_text_controls_and_banners(prefix):
    tokens = _tokens(SHEETS['monitor.html'])
    def color(role):
        return tokens['--' + prefix + role]
    for surface in ('bg', 'panel', 'raise', 'sel'):
        for foreground in ('ink', 'soft', 'accent', 'ok', 'warn', 'crit', 'violet'):
            assert _contrast(color(foreground), color(surface)) >= 4.5, (prefix, foreground, surface)
        assert _contrast(color('line2'), color(surface)) >= 3
    for background in ('accent', 'hover'):
        assert _contrast(color('onaccent'), color(background)) >= 4.5
    for status in ('warn', 'crit'):
        assert _contrast(color(status), color(status + 'bg')) >= 4.5


def test_light_token_overrides_are_shared_and_complete():
    overrides = [re.search(r':root\[data-theme="light"\]\{([^}]+)\}', SHEETS[name]).group(1)
                 for name in ('monitor.html', 'settings.html')]
    assert overrides[0] == overrides[1]
    tokens = _tokens(SHEETS['monitor.html'])
    for role in tokens:
        if not role.startswith('--light'):
            assert f'{role}:var(--light{role[2:]});' in overrides[0]


def test_theme_preferences_follow_system_and_survive_unavailable_storage():
    import json
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for theme behavior tests')
    scripts = [re.search(r'<script id="theme-script">(.*?)</script>', (PKG / name).read_text(), re.S).group(1)
               for name in ('monitor.html', 'settings.html')]
    assert scripts[0] == scripts[1]
    harness = r'''
const assert=require('node:assert/strict'), vm=require('node:vm');
function run(saved,blocked){
  const events={},docEvents={},selectEvents={},writes=[];
  const select={value:'',addEventListener:(k,f)=>selectEvents[k]=f};
  const document={documentElement:{dataset:{}},getElementById:()=>select,addEventListener:(k,f)=>docEvents[k]=f};
  const media={matches:true,addEventListener:(k,f)=>events.system=f};
  const window={matchMedia:()=>media,dispatchEvent:()=>{},addEventListener:(k,f)=>events[k]=f};
  const localStorage={getItem:()=>{if(blocked)throw Error('blocked');return saved;},setItem:(k,v)=>{if(blocked)throw Error('blocked');writes.push(v);}};
  vm.runInNewContext(SCRIPT,{document,window,localStorage,Event:class{}});
  docEvents.DOMContentLoaded();
  assert.equal(document.documentElement.dataset.theme,saved==='light'&&!blocked?'light':'dark');
  select.value='light';selectEvents.change();assert.equal(document.documentElement.dataset.theme,'light');
  media.matches=true;events.system();assert.equal(document.documentElement.dataset.theme,'light');
  select.value='system';selectEvents.change();media.matches=false;events.system();
  assert.equal(document.documentElement.dataset.theme,'light');
  media.matches=true;events.system();assert.equal(document.documentElement.dataset.theme,'dark');
  events.storage({key:'waveraider-theme',newValue:'light'});assert.equal(select.value,'light');
  events.storage({key:'waveraider-theme',newValue:null});assert.equal(select.value,'system');
}
run(null,false);run('light',false);run('invalid',false);run(null,true);
'''
    result = subprocess.run([node, '-e', 'const SCRIPT=' + json.dumps(scripts[0]) + ';\n' + harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
