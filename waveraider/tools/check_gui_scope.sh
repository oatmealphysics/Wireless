#!/bin/sh
# Checks that every path in the given list is a GUI file the gui-refresh branch may change.
# Usage: tools/check_gui_scope.sh changed-files.txt   (one repository path per line)
# Exit 0 when every path is allowed, 1 otherwise. Used by .github/workflows/gui-scope.yml.
set -eu
list="${1:?path list required}"
bad=0
while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    waveraider/monitor.html|waveraider/settings.html) ;;
    waveraider/inspect_rf.js|waveraider/inspect_rf.css|waveraider/spectrum_display.js) ;;
    waveraider/brand/*) ;;
    docs/usage.md|docs/branding.md|CHANGELOG.md) ;;
    tests/test_monitor.py|tests/test_settings.py|tests/test_styles.py|tests/test_navigation.py) ;;
    tests/test_spectrum_ui.py|tests/test_inspect_rf.py|tests/test_spectrum_display.py) ;;
    *) echo "out of scope for the GUI branch: $path"; bad=1 ;;
  esac
done < "$list"
if [ "$bad" -ne 0 ]; then
  echo "The gui-refresh branch may change only the GUI files listed in CLAUDE.md."
  exit 1
fi
echo "All changed files are GUI files."
