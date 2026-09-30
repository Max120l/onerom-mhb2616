#!/usr/bin/env bash
# Fill WORK/roms and WORK/corpus: the Infoserver software packages and the
# monitor/BASIC ROM images the audition and the build need.
#
#   shelf/fetch_corpus.sh WORK
#
# Packages come from https://pmd85.borik.net/wiki/Download; the ROMs from
# the GPMD85Emulator repository's rom/ directory (its monit3.rom is the
# monit3B image the rest of the project calls by that name).
set -euo pipefail
WORK=${1:?usage: fetch_corpus.sh WORK}
mkdir -p "$WORK/archives" "$WORK/corpus" "$WORK/roms"

PACKAGES="Basic Develop Editors Game_Lemmings Games_4004-482
Games_Libor_Lasota Games_Other Games_RM-TEAM Games_VBG Graphics Karel Logo
Musics Pascal Saso vyukove_programy_UPnpBB"

for pkg in $PACKAGES; do
    zip="$WORK/archives/$pkg.zip"
    if [ ! -s "$zip" ]; then
        page="https://pmd85.borik.net/wiki/Package:$pkg.zip"
        url=$(curl -fsS "$page" \
              | grep -oE 'href="https://pmd85.borik.net/\?action=download[^"]*"' \
              | head -1 | sed 's/^href="//; s/"$//; s/&amp;/\&/g')
        [ -n "$url" ] || { echo "no download link on $page" >&2; exit 1; }
        curl -fsSL "$url" -o "$zip"
    fi
    rm -rf "$WORK/corpus/$pkg"
    python3 -m zipfile -e "$zip" "$WORK/corpus/$pkg"
    echo "$pkg: $(stat -c%s "$zip") bytes"
done

emu="$WORK/gpmd85emulator"
[ -d "$emu" ] || git clone --depth 1 \
    https://github.com/mborik/GPMD85Emulator "$emu"
cp "$emu/rom/monit3.rom" "$WORK/roms/monit3B.rom"
for f in monit1.rom monit2.rom monit2A.rom basic3.rmm basic2A.rmm; do
    cp "$emu/rom/$f" "$WORK/roms/"
done
echo "corpus: $(find "$WORK/corpus" -iname '*.ptp' | wc -l) tapes; roms in $WORK/roms"
