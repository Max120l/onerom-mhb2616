#!/usr/bin/env bash
# Run the factory over every tape in WORK/corpus -> WORK/audition.
#
#   shelf/audition.sh WORK
#
# Slow: the full corpus is ~260 programs, each booted in two environments
# and nudged with keypresses; expect an hour or more.  The --trust names
# are games that play fine on the bench but trip the gauntlet under one
# synthetic noise stream (see docs/ROM-module.md, "The bench outranks the
# emulator").
set -euo pipefail
WORK=${1:?usage: audition.sh WORK}
HERE=$(cd "$(dirname "$0")" && pwd)
mapfile -t TAPES < <(find "$WORK/corpus" -iname '*.ptp' | sort)
python3 "$HERE/../tools/shelf_factory.py" \
    --monitor3 "$WORK/roms/monit3B.rom" \
    --monitors-dir "$WORK/roms" \
    --trust KUBANOID --trust ATOMIX --trust SOLITER \
    --out "$WORK/audition" \
    "${TAPES[@]}"
