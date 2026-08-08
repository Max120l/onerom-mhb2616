"""Tests for the image tools: generation round-trips, selftest diagnosis."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import selftest            # noqa: E402
import check_selftest      # noqa: E402


def run_gen(tmp_path, *args):
    out = tmp_path / "rom_images.c"
    res = subprocess.run(
        [sys.executable, str(TOOLS / "gen_rom_images.py"), "-o", str(out), *args],
        capture_output=True, text=True)
    return res, out


def parse_banks(text):
    """The four 2048-byte banks back out of the generated C."""
    body = text[text.index("mhb_banks"):]
    body = "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))
    rows = re.findall(r"0x([0-9A-F]{2})\b", body)
    data = bytes(int(h, 16) for h in rows)
    assert len(data) == 4 * 2048
    return [data[b * 2048:(b + 1) * 2048] for b in range(4)]


def parse_present(text):
    return int(re.search(r"mhb_bank_present = 0x([0-9A-F]{2})", text).group(1), 16)


def test_monitor_split(tmp_path):
    image = bytes((i * 31 + 7) & 0xFF for i in range(8192))
    src = tmp_path / "monitor.bin"
    src.write_bytes(image)
    res, out = run_gen(tmp_path, "--monitor", str(src))
    assert res.returncode == 0, res.stderr
    banks = parse_banks(out.read_text())
    for b in range(4):
        assert banks[b] == image[b * 2048:(b + 1) * 2048]
    assert parse_present(out.read_text()) == 0x0F


def test_partial_chip_set(tmp_path):
    dump = bytes(range(256)) * 8
    src = tmp_path / "mz3.bin"
    src.write_bytes(dump)
    res, out = run_gen(tmp_path, "--bank", "2", str(src))
    assert res.returncode == 0, res.stderr
    text = out.read_text()
    banks = parse_banks(text)
    assert banks[2] == dump
    assert banks[0] == bytes([0xFF]) * 2048
    assert parse_present(text) == 0x04


def test_wrong_size_rejected(tmp_path):
    src = tmp_path / "short.bin"
    src.write_bytes(b"\x00" * 100)
    res, _ = run_gen(tmp_path, str(src))
    assert res.returncode != 0
    res, _ = run_gen(tmp_path, "--monitor", str(src))
    assert res.returncode != 0


def test_selftest_emit_and_check(tmp_path):
    res, out = run_gen(tmp_path, "--selftest")
    assert res.returncode == 0, res.stderr
    banks = parse_banks(out.read_text())
    for b in range(4):
        assert banks[b] == selftest.image(b)
        assert not check_selftest.check_bank(banks[b], b)


def test_selftest_marker_disambiguates_banks():
    # No byte pair is valid for two different (bank, word) pairs.
    seen = {}
    for b in range(4):
        img = selftest.image(b)
        for a in range(0, 2048, 2):
            pair = (img[a], img[a + 1])
            assert pair not in seen, f"pair collision {seen[pair]} vs {(b, a)}"
            seen[pair] = (b, a)


def test_check_selftest_diagnoses_stuck_data_line():
    img = bytearray(selftest.image(1))
    for a in range(len(img)):
        img[a] |= 0x08          # D3 stuck high
    bad = check_selftest.check_bank(bytes(img), 1)
    assert bad
    # Every mismatch differs in D3.
    assert all((got ^ want) & 0x08 for _, got, want in bad)


def test_check_selftest_clean_roundtrip(tmp_path, capsys):
    img = selftest.image(0) + selftest.image(1) + selftest.image(2) + selftest.image(3)
    dump = tmp_path / "all.bin"
    dump.write_bytes(img)
    res = subprocess.run(
        [sys.executable, str(TOOLS / "check_selftest.py"), str(dump)],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.count("clean") == 4


def test_checksum_compare(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(bytes(2048))
    b.write_bytes(bytes(2047) + b"\x01")
    res = subprocess.run(
        [sys.executable, str(TOOLS / "rom_checksum.py"), "--compare",
         str(a), str(b)], capture_output=True, text=True)
    assert res.returncode == 1
    assert "0x07FF" in res.stdout
    res = subprocess.run(
        [sys.executable, str(TOOLS / "rom_checksum.py"), "--compare",
         str(a), str(a)], capture_output=True, text=True)
    assert res.returncode == 0


def _distinct_banks():
    # Four banks different enough that identification gaps are wide.
    return [bytes((i * 7 + b * 53 + 11) & 0xFF for i in range(2048)) for b in range(4)]


def run_checksum(*args):
    return subprocess.run(
        [sys.executable, str(TOOLS / "rom_checksum.py"), *map(str, args)],
        capture_output=True, text=True)


def test_identify_exact_and_degraded(tmp_path):
    banks = _distinct_banks()
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"".join(banks))

    clean = tmp_path / "clean.bin"
    clean.write_bytes(banks[2])
    dying = bytearray(banks[1])
    for i in range(0, 400, 3):          # ~130 corrupt bytes: a failing chip
        dying[i] ^= 0xA5
    bad = tmp_path / "dying.bin"
    bad.write_bytes(bytes(dying))

    res = run_checksum("--identify", ref, clean, bad)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "-> bank 2, exact" in res.stdout
    assert "-> bank 1, with" in res.stdout


def test_identify_rejects_garbage(tmp_path):
    banks = _distinct_banks()
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"".join(banks))
    junk = tmp_path / "junk.bin"
    junk.write_bytes(bytes((i * 251 + 17) & 0xFF for i in range(2048)))
    res = run_checksum("--identify", ref, junk)
    assert res.returncode == 1
    assert "no clear match" in res.stdout


def test_identify_by_bit_decay(tmp_path):
    # Past ~25% of bytes wrong every bank looks equally unlike the dump, so
    # the byte count gives up.  Bits do not: this chip keeps one byte in 23
    # and reads 00 everywhere else, which is hopeless to match by bytes and
    # still names its bank outright.
    banks = _distinct_banks()
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"".join(banks))
    wreck = tmp_path / "wreck.bin"
    wreck.write_bytes(bytes(b if i % 23 == 0 else 0
                            for i, b in enumerate(banks[3])))
    res = run_checksum("--identify", ref, wreck)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "-> bank 3, from" in res.stdout
    assert "surviving bits alone" in res.stdout


def test_identify_by_bit_decay_the_other_direction(tmp_path):
    # The mirror failure -- a part rotting towards FF rather than 00.  Same
    # reasoning, opposite polarity, and the tool must say which it saw.
    banks = _distinct_banks()
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"".join(banks))
    wreck = tmp_path / "wreck.bin"
    wreck.write_bytes(bytes(b if i % 23 == 0 else 0xFF
                            for i, b in enumerate(banks[2])))
    res = run_checksum("--identify", ref, wreck)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "gaining set bits" in res.stdout
    assert "-> bank 2, from" in res.stdout


def test_identify_refuses_a_chip_with_too_little_left(tmp_path):
    # Consistent with exactly one bank, but on so few surviving bits that
    # agreement could be luck.  Naming it anyway is the failure mode this
    # threshold exists to prevent.
    ref = tmp_path / "ref.bin"
    ref.write_bytes(bytes([0x01]) * 2048 + bytes([0x02]) * 2048)
    faint = tmp_path / "faint.bin"
    faint.write_bytes(bytes(0x01 if i < 3 else 0 for i in range(2048)))
    res = run_checksum("--identify", ref, faint)
    assert res.returncode == 1
    assert "too little to name it" in res.stdout


def test_identify_refuses_a_chip_with_nothing_left(tmp_path):
    # All-zero matches every bank trivially; that is not an identification.
    banks = _distinct_banks()
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"".join(banks))
    dead = tmp_path / "dead.bin"
    dead.write_bytes(bytes(2048))
    res = run_checksum("--identify", ref, dead)
    assert res.returncode == 1
    assert "no clear match" in res.stdout
