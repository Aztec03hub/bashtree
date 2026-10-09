#!/bin/bash
# The ONLY way to run bench/differential.py over several seeds or modes.
#
#   bench/sweep.sh COUNT "SEEDS" "MODES"       e.g.  bench/sweep.sh 4000 "1 2 3" "gen mut both"
#
# One safe-run for the whole sweep: sequential, one differential at a time, under
# a task cap and a time limit, killed as a whole at the end. Never write your own
# loop around differential.py, and never run two sweeps. See bench/safe-run.sh.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
COUNT=${1:?count}; SEEDS=${2:?seeds}; MODES=${3:-gen}
PY=${PYTHON:-python3}   # needs tree-sitter and tree-sitter-bash importable
RUN="$HERE/bench/safe-run.sh"
if [ "${SWEEP_INNER:-}" != 1 ]; then
  exec "$RUN" -t 1500 -n 300 -- env SWEEP_INNER=1 JOBS=4 BASHTREE_ROOT="${BASHTREE_ROOT:-$HERE}" "$0" "$@"
fi
for seed in $SEEDS; do
  for mode in $MODES; do
    "$PY" -I "$HERE/bench/differential.py" "$COUNT" "$seed" "$mode" 2>&1 | head -40
  done
done
