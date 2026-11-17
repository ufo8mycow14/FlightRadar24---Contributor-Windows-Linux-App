#!/usr/bin/env bash
set -euo pipefail

app="${1:?usage: check-linux-desktop.sh /path/to/FlightRadar24}"
app="$(readlink -f "$app")"
if ! command -v xvfb-run >/dev/null || ! command -v xdotool >/dev/null; then
  echo 'Install xvfb and xdotool to run the frozen desktop lifecycle check.' >&2
  exit 2
fi

# Keep the app, window lookup, and close request in one X server.
xvfb-run -a bash -s -- "$app" <<'INNER'
set -euo pipefail
app="$1"
test_root="$(mktemp -d)"
session="$test_root/session.json"
openbox >/dev/null 2>&1 &
wm_pid=$!
"$app" --data-dir "$test_root/profile" --session-file "$session" &
pid=$!
cleanup() {
  if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  kill "$wm_pid" 2>/dev/null || true
  rm -rf "$test_root"
}
trap cleanup EXIT

ready=0
for _ in $(seq 1 150); do
  if [[ -s "$session" ]] && grep -q '"ready": true' "$session"; then ready=1; break; fi
  if ! kill -0 "$pid" 2>/dev/null; then break; fi
  sleep 0.1
done
if [[ "$ready" != 1 ]]; then
  echo 'Frozen FlightRadar24 desktop did not reach its native ready state.' >&2
  exit 1
fi

window=''
for _ in $(seq 1 50); do
  window="$(xdotool search --name FlightRadar24 2>/dev/null | head -n1 || true)"
  [[ -n "$window" ]] && break
  sleep 0.1
done
if [[ -z "$window" ]]; then
  echo 'The native FlightRadar24 window was not visible under Xvfb.' >&2
  exit 1
fi
xdotool windowclose "$window"
stopped=0
for _ in $(seq 1 100); do
  if ! kill -0 "$pid" 2>/dev/null; then stopped=1; break; fi
  sleep 0.1
done
if [[ "$stopped" != 1 ]]; then
  echo 'The native window did not complete a clean shutdown after close.' >&2
  exit 1
fi
wait "$pid"
if [[ -e "$session" ]]; then
  echo 'Native window closed but the owned readiness record was not removed.' >&2
  exit 1
fi
echo 'Frozen Linux desktop startup and window-close shutdown passed.'
INNER
