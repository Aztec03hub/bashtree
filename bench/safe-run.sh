#!/bin/bash
# safe-run.sh - the ONLY way to run anything that executes shell text in bulk
# (differential tests against bash, fuzzers, sweeps over seeds or modes).
#
#   safe-run.sh [-t SECONDS] [-n MAXTASKS] -- COMMAND [ARGS...]
#
# What it enforces, so a runaway cannot take the machine down (2026-10-09: an
# uncapped sweep reached 8,800 processes, load 1,500, and broke tmux):
#   1. ONE run at a time on the whole machine (flock); a second copy waits or
#      is refused (-w to wait, default refuse).
#   2. A task cap: the command runs in its own systemd scope with TasksMax
#      (default 300). Past it, fork fails inside the scope, not on the machine.
#   3. A hard wall-clock limit (default 900 s); then the WHOLE scope is killed.
#   4. The scope is stopped when the command ends, however it ends, so no
#      background job survives it.
#   5. A process census before and after, printed, and a refusal to start when
#      the machine is already busy (over 2,000 processes or load over 40).
# It runs nothing itself; it only wraps COMMAND. Refuses without systemd-run.
set -u
TIMEOUT=900; TASKS=300; WAIT=0
while getopts "t:n:w" o; do
  case $o in t) TIMEOUT=$OPTARG ;; n) TASKS=$OPTARG ;; w) WAIT=1 ;; *) exit 2 ;; esac
done
shift $((OPTIND - 1))
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "usage: safe-run.sh [-t SECONDS] [-n MAXTASKS] [-w] -- COMMAND [ARGS...]" >&2; exit 2; }
[ "$TASKS" -le 1000 ] || { echo "safe-run: -n above 1000 is not allowed" >&2; exit 2; }
command -v systemd-run >/dev/null || { echo "safe-run: systemd-run is required; not running" >&2; exit 3; }

procs() { ps -eo pid= | wc -l; }
load() { cut -d. -f1 /proc/loadavg; }
if [ "$(procs)" -gt 2000 ] || [ "$(load)" -gt 40 ]; then
  echo "safe-run: machine is busy (processes $(procs), load $(load)); not starting" >&2
  exit 4
fi

exec 9>/tmp/safe-run.lock
if [ "$WAIT" = 1 ]; then flock 9; else
  flock -n 9 || { echo "safe-run: another safe-run is already going; not starting a second" >&2; exit 5; }
fi

UNIT="safe-run-$$-$(date +%s)"
echo "safe-run: unit $UNIT, tasks<=$TASKS, limit ${TIMEOUT}s, processes before: $(procs)" >&2
stop() { systemctl --user stop "$UNIT.scope" 2>/dev/null; }
trap stop EXIT INT TERM
systemd-run --user --scope --quiet --unit="$UNIT" -p TasksMax="$TASKS" -p RuntimeMaxSec="$TIMEOUT" \
  env SAFE_RUN=1 timeout --kill-after=10 "$TIMEOUT" "$@"
rc=$?
stop
sleep 1
echo "safe-run: exit $rc, processes after: $(procs), load $(load)" >&2
exit $rc
