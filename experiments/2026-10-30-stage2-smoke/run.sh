#!/usr/bin/env bash
# Smoke test: source window -> engine -> protected view, recorded like a meeting app would.
# Run from the repo root: bash experiments/2026-10-30-stage2-smoke/run.sh [seed] [seconds] [--faults]
set -u
seed=${1:-1}
seconds=${2:-25}
faults=${3:-}
out=/d/Veil-cache/stage2-smoke
py=.venv/Scripts/python
here=experiments/2026-10-30-stage2-smoke
rm -rf "$out" && mkdir -p "$out"

$py $here/source.py "$seed" &
src=$!
sleep 3
$py -m engine --window "VEIL test source" $faults > "$out/engine.log" 2>&1 &
eng=$!
# wait for the protected view to exist (the first start can take >8 s), then
# let the first frames through OCR; the view stays black meanwhile
for _ in $(seq 30); do
  powershell -NoProfile -Command "if (Get-Process | Where-Object { \$_.MainWindowTitle -eq 'VEIL protected view' }) { exit 0 } else { exit 1 }" && break
  sleep 1
done
sleep 3

$py $here/record.py "$out/protected" "$seconds"
$py $here/record.py "$out/raw" 8 "VEIL test source"  # positive control: unprotected
kill $eng $src
wait 2>/dev/null

$py -m bench.check_recording "$out/protected" --seed "$seed"
$py -m bench.check_recording "$out/raw" --seed "$seed"
