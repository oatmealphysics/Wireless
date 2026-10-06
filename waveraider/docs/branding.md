# WaveRaider branding

The official WaveRaider logo combines a W-shaped radio wave with an upward aircraft
motif. The approved horizontal logo is [`logo.png`](../waveraider/brand/logo.png);
[`icon.png`](../waveraider/brand/icon.png) is its compact app and browser-tab emblem.
Use the supplied full logo for project identification and the emblem beside the
WaveRaider name in compact application headers. Keep the proportions intact.

The visual direction follows the application palette: clear blue (`#8bcbff`),
soft white (`#e6edf3`) and charcoal navy (`#111820` / `#1a2530`). The supplied PNGs
include their dark backgrounds. They are raster assets, not vector artwork.

Both assets are packaged with the application and served locally; branding requires
no external fonts, image host or internet connection. The emblem is decorative next
to the accessible WaveRaider text in Monitor and Settings.

## GUI layout

Monitor and Settings share the same local system font, color tokens, navigation,
focus treatment and controls. The compact header holds navigation and session
controls. Monitor uses a bordered observation list and a separate evidence panel;
at narrow widths the panels stack and tables scroll within their own containers.
Receiver and technical disclosures use native keyboard-accessible `details` elements.
Settings groups controls into a content panel beside section navigation, which wraps
above the content on narrow screens. Warning colors describe sample status, evidence
limits and errors, never an inferred threat level. No external assets are fetched.

## Field themes

The header's Theme selector offers System, Dark and Light. System follows the
operating system; an explicit choice persists in this browser across pages and
reloads. With browser storage blocked, switching still works for the current page.
The light palette uses pale neutral surfaces, dark ink and a deeper blue accent.
Both themes use the same semantic tokens, including warning/error backgrounds and
spectrum traces. The waterfall retains its independent quantitative heat scale.

Primary and secondary text, status labels and action colors meet 4.5:1 against
all base and selected surfaces; essential control outlines meet 3:1. Filled
buttons have a dedicated contrasting foreground. Automated tests check these
pairs for both themes. This does not establish outdoor-device readability or
complete accessibility: validate on the intended Android display in field light.

Use color alongside words, state markers and focus outlines. Green describes
receiver/system health, never absence of drones; red and amber communicate errors
and attention, not an inferred aircraft threat. Keep the current sample/evidence
limitations explicit. Theme roles can carry into a future Android interface.

Design references: [Android color roles](https://developer.android.com/design/ui/mobile/guides/styles/color),
[WCAG text contrast](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum),
[non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
and [use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).
