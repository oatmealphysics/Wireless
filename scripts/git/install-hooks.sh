#!/usr/bin/env bash
# One-time: link the shared git hooks into .git/hooks/ for this clone.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
ln -sf ../../scripts/git/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push 2>/dev/null || true
echo "Installed pre-push hook -> .git/hooks/pre-push (now `git push` auto-commits captures/)."
