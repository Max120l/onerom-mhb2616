# The shelf: building the multiload flash images

Two One ROM flash images for the PMD 85-3 ROM module, each a menu of
directories over up to 128 pages of 16 KB:

- **games** — the verified games, in alphabetical directories
  (`games.json`)
- **apps** — more games, editors, graphics, development tools, music
  (`apps.json`)

Both share a SYSTEM directory: BASIC-G 3.0, BASIC 2A, two test cards,
the module scanner, the banner, and FLASH CHECK (run it first after every
flash; it checksums every page against sums baked into the image).

## Rebuilding

Pick a work directory outside the repo — it holds a few hundred MB of
downloads and generated files.

```
shelf/fetch_corpus.sh ~/pmd85-work        # packages + ROMs
shelf/audition.sh     ~/pmd85-work        # slow: an hour or more
PICO_SDK_PATH=~/pico-sdk \
shelf/build_images.py games --work ~/pmd85-work
shelf/build_images.py apps  --work ~/pmd85-work
```

The images land in `WORK/out/` as `mhb2616-24F-MULTILOAD-{games,apps}.uf2`
(plus a `.bin` and screenshots). Needs `arm-none-eabi-gcc`, `cmake`,
`ninja` and the Pico SDK for the firmware step.

`audition.sh` only needs re-running when the corpus or the factory
(`tools/shelf_factory.py`) changes. Editing the curation files and
re-running `build_images.py` is enough to change what ships.

## Curation files

Each pick names a program by its audition name (the name in
`WORK/audition/report.md`), with an optional display `name` and an
optional `override` merged into the audition entry (`null` deletes a
key). The monitor-cargo games (BOULDER DASH, CROSSFIRE, COBRA) carry
`{"mode": null, "overlay": {"file": "monit1.rom", "at": "0x8000"}}`:
a native boot with the PMD 85-1 monitor at 8000h. The build also runs
that monitor's startup to ship the variables it keeps in the video
margins (font address, cursor, key table), and the loader switches to
AllRAM before the jump (see docs/ROM-module.md).

The games image is sorted by display name and split into directories of
11; the directory labels follow from the names. `build_images.py` stops
if an image would exceed 128 pages (FLASH CHECK's grid) — drop a pick to
make room.
