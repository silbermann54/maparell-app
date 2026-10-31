#!/usr/bin/env python3
"""Builds Maparell/Resources/Peaks.bin from an OpenStreetMap planet file (DEC-158, DEC-162).

Usage: scripts/peaks/build-peaks.py <planet-or-extract.osm.pbf> [output]

Needs pyosmium; it lives only in the git-ignored build/peaks-venv, never in the app:
  python3 -m venv build/peaks-venv && build/peaks-venv/bin/pip install osmium
  build/peaks-venv/bin/python scripts/peaks/build-peaks.py build/planet/planet-YYMMDD.osm.pbf
The weekly planet file comes from a planet.openstreetmap.org mirror (check its .md5). Reads named
`natural=peak` / `natural=volcano` nodes.

File format (little endian), read by PeakFile.swift:
  header  "MPK1", version u32 (planet date YYYYMMDD), tile count u32, peak count u32
  index   per tile, sorted by key: key u32, offset u32, compressed length u32, raw length u32, peak count u32
          key = (floor(lat) + 90) * 360 + (floor(lon) + 180); offset counts from the end of the index
  tiles   raw DEFLATE of records: id i64, lat i32 (1e-6°), lon i32, height i16 (-32768 = unknown),
          name length u8, name UTF-8
Data © OpenStreetMap contributors, ODbL 1.0.
"""

import datetime
import math
import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "Maparell" / "Resources" / "Peaks.bin"
UNKNOWN_HEIGHT = -32768
MAX_NAME_BYTES = 255
HEIGHT = re.compile(r"^\s*(-?\d{1,3}(?:,\d{3})+|-?\d+(?:[.,]\d+)?)\s*(m|meters?|ft|feet|')?\s*$", re.IGNORECASE)


def parse_height(raw):
    """Meters as an int, or UNKNOWN_HEIGHT for missing, unparsable or implausible values."""
    if not raw:
        return UNKNOWN_HEIGHT
    match = HEIGHT.match(raw.split(";")[0])
    if not match:
        return UNKNOWN_HEIGHT
    number, unit = match.groups()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", number):
        number = number.replace(",", "")
    value = float(number.replace(",", "."))
    if unit and unit.lower() in ("ft", "feet", "'"):
        value *= 0.3048
    if not -450 <= value <= 8900:
        return UNKNOWN_HEIGHT
    return round(value)


def clean_name(raw):
    name = " ".join(raw.split())
    data = name.encode("utf-8")
    if len(data) <= MAX_NAME_BYTES:
        return data
    return data[:MAX_NAME_BYTES].decode("utf-8", "ignore").encode("utf-8")


def tile_key(lat, lon):
    return (math.floor(lat) + 90) * 360 + (min(math.floor(lon), 179) + 180)


def read_peaks(pbf):
    """{osm id: (lat, lon, height, encoded name)} of every named peak or volcano node."""
    import osmium

    tags = osmium.filter.TagFilter(("natural", "peak"), ("natural", "volcano"))
    peaks = {}
    for node in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(tags):
        name = clean_name(node.tags.get("name", ""))
        location = node.location
        if name and location.valid() and -90 <= location.lat < 90:
            peaks[node.id] = (location.lat, location.lon, parse_height(node.tags.get("ele")), name)
    return peaks


def file_version(pbf):
    """YYYYMMDD from a planet-YYMMDD file name, else today."""
    match = re.search(r"planet-(\d{6})", Path(pbf).name)
    if match:
        return int("20" + match.group(1))
    return int(datetime.date.today().strftime("%Y%m%d"))


def write(peaks, version, output):
    tiles = {}
    for osm_id, (lat, lon, height, name) in sorted(peaks.items()):
        record = struct.pack("<qiihB", osm_id, round(lat * 1e6), round(lon * 1e6), height, len(name)) + name
        tiles.setdefault(tile_key(lat, lon), []).append(record)
    index, blobs, offset = [], [], 0
    for key in sorted(tiles):
        raw = b"".join(tiles[key])
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        blob = compressor.compress(raw) + compressor.flush()
        index.append(struct.pack("<IIIII", key, offset, len(blob), len(raw), len(tiles[key])))
        blobs.append(blob)
        offset += len(blob)
    header = b"MPK1" + struct.pack("<III", version, len(index), len(peaks))
    output.write_bytes(header + b"".join(index) + b"".join(blobs))
    known = sum(1 for peak in peaks.values() if peak[2] != UNKNOWN_HEIGHT)
    size = output.stat().st_size / 1_000_000
    print(f"{len(peaks)} peaks ({known} with height) in {len(index)} tiles, {size:.1f} MB → {output}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT
    write(read_peaks(source), file_version(source), destination)
