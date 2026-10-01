#!/usr/bin/env bash
# One-shot webui diagnostic for the laptop (borealis). Run:  bash webui_diag.sh
# It checks: (1) is the python webui process alive, (2) is port 8090 bound,
# (3) can localhost:8090 reach the page + API, (4) what does the API say
# (radio OK or error). Paste the whole output back to tune the next step.
set -u
PORT="${1:-8090}"
echo "=== webui diagnostic (port $PORT) ==="
echo ""
echo "--- 1) is the webui process running? ---"
if ps aux | grep -E "elrs_sdr.*webui" | grep -v grep; then
  echo "  -> YES, process is alive"
else
  echo "  -> NO webui process found. Did the command exit with an error?"
  echo "     Re-run it in the foreground and paste the last 20 lines."
fi
echo ""
echo "--- 2) is port $PORT bound? ---"
if command -v ss >/dev/null 2>&1; then
  ss -ltnp 2>/dev/null | grep -E ":$PORT\b" || echo "  -> NOT bound to $PORT"
elif command -v netstat >/dev/null 2>&1; then
  netstat -ltnp 2>/dev/null | grep -E ":$PORT\b" || echo "  -> NOT bound to $PORT"
else
  echo "  (no ss/netstat available; skipping)"
fi
echo ""
echo "--- 3) can localhost reach the page? ---"
code=$(curl -sS -m 5 -o /tmp/wu_page.html -w "%{http_code}" "http://127.0.0.1:$PORT/" 2>/tmp/wu_curl.err)
if [ "$code" = "200" ]; then
  echo "  -> HTTP 200, $(wc -c < /tmp/wu_page.html) bytes. The server IS serving the page."
  echo "     => If the browser still won't load it, it's a BROWSER/URL issue (see 4)."
else
  echo "  -> HTTP $code  ($(cat /tmp/wu_curl.err 2>/dev/null | head -1))"
  echo "     => The server isn't reachable on 127.0.0.1:$PORT."
fi
echo ""
echo "--- 4) what does the API say? (radio OK or error) ---"
curl -sS -m 5 "http://127.0.0.1:$PORT/api/state" 2>&1 | head -c 500
echo ""
echo ""
echo "=== quick browser checks ==="
echo "  - open  http://localhost:$PORT   (NOT a different port)"
echo "  - also try  http://127.0.0.1:$PORT"
echo "  - if a VPN/proxy is on, it may intercept localhost — disable it"
echo "  - if you opened it from the ENCLAVE (not the laptop), it won't reach the"
echo "    laptop's localhost — open it on the laptop's own browser."
echo "=== done ==="
