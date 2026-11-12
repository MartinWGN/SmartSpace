"""Download the GLOBAL-12 (NORAD 49772) 2025 TLE history from Space-Track into input/."""

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

NORAD_CAT_ID, YEAR = 49772, 2025
BASE_URL = "https://www.space-track.org"
ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=os.environ.get("SPACETRACK_USER"))
    parser.add_argument("--password", default=os.environ.get("SPACETRACK_PASS"))
    parser.add_argument(
        "--format", choices=["tle", "json", "csv", "kvn", "xml"], default="json"
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def query_url(fmt):
    # The end bound is 2026-01-01 so an epoch late on 31 December is not dropped by the server.
    return (
        f"{BASE_URL}/basicspacedata/query/class/gp_history/NORAD_CAT_ID/{NORAD_CAT_ID}/"
        f"EPOCH/2025-01-01--2026-01-01/orderby/EPOCH%20asc/format/{fmt}"
    )


def epoch_year(line1):
    try:
        yy = int(line1[18:20])
    except (ValueError, IndexError):
        return None
    return 2000 + yy if yy < 57 else 1900 + yy


def filter_tle(text):
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    kept, i = [], 0
    while i < len(lines):
        if (
            lines[i].startswith("1 ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("2 ")
        ):
            block, step = [lines[i], lines[i + 1]], 2
            line1 = lines[i]
        elif (
            i + 2 < len(lines)
            and lines[i + 1].startswith("1 ")
            and lines[i + 2].startswith("2 ")
        ):
            block, step, line1 = lines[i : i + 3], 3, lines[i + 1]
        else:
            i += 1
            continue
        if epoch_year(line1) == YEAR:
            kept.extend(block)
        i += step
    return "\n".join(kept) + ("\n" if kept else "")


def check_pair(line1, line2):
    for prefix, line in (("1 ", line1), ("2 ", line2)):
        if not isinstance(line, str) or len(line) != 69 or not line.startswith(prefix):
            raise ValueError("Invalid TLE line")
        if int(line[2:7]) != NORAD_CAT_ID:
            raise ValueError("TLE is for a different satellite")
        checksum = sum(int(c) if c.isdigit() else int(c == "-") for c in line[:68]) % 10
        if not line[68].isdigit() or checksum != int(line[68]):
            raise ValueError("Invalid TLE checksum")
    if epoch_year(line1) != YEAR:
        raise ValueError("TLE epoch is outside the requested year")


def records_from(text, fmt):
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(text)))
    if fmt == "kvn":
        records, current = [], {}
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key == "CCSDS_OMM_VERS" and current:
                records.append(current)
                current = {}
            current[key] = value
        if current:
            records.append(current)
        return records
    root = ET.fromstring(text)
    records = []
    for segment in (
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "segment"
    ):
        fields = {}
        for node in segment.iter():
            name = node.tag.rsplit("}", 1)[-1]
            fields[node.attrib.get("parameter", name)] = (node.text or "").strip()
        records.append(fields)
    return records


def validate_payload(text, fmt):
    """Reject an HTTP 200 body that is an error page, empty, or for another satellite."""
    if fmt == "tle":
        text = filter_tle(text)
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        pairs = [
            (line, lines[i + 1])
            for i, line in enumerate(lines)
            if line.startswith("1 ") and i + 1 < len(lines)
        ]
        if not pairs:
            raise ValueError("Response contains no TLE records for the requested year")
        for line1, line2 in pairs:
            check_pair(line1, line2)
        return text
    records = json.loads(text) if fmt == "json" else records_from(text, fmt)
    if fmt == "json" and not isinstance(records, list):
        raise ValueError("Expected a JSON array of orbital records")
    if not records:
        raise ValueError("Response contains no orbital records")
    in_year = 0
    for record in records:
        if (
            not isinstance(record, dict)
            or int(record.get("NORAD_CAT_ID", -1)) != NORAD_CAT_ID
        ):
            raise ValueError("Missing or incorrect NORAD_CAT_ID")
        if datetime.fromisoformat(record["EPOCH"].replace("Z", "+00:00")).year == YEAR:
            in_year += 1
        if fmt == "json" or "TLE_LINE1" in record or "TLE_LINE2" in record:
            check_pair(record.get("TLE_LINE1", ""), record.get("TLE_LINE2", ""))
    if not in_year:
        raise ValueError("Response contains no records for the requested year")
    return text


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    args = parse_args()
    if not args.username or not args.password:
        print(
            "Set SPACETRACK_USER and SPACETRACK_PASS, or pass --username and --password.",
            file=sys.stderr,
        )
        return 2
    output = args.output or ROOT / "input" / f"GLOBAL-12_49772_2025.{args.format}"
    try:
        with requests.Session() as session:
            login = session.post(
                f"{BASE_URL}/ajaxauth/login",
                data={"identity": args.username, "password": args.password},
                timeout=60,
            )
            login.raise_for_status()
            response = session.get(query_url(args.format), timeout=180)
            response.raise_for_status()
            text = validate_payload(response.text, args.format)
        write_text(output, text)
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
        ET.ParseError,
        OSError,
    ) as exc:
        print(f"Download rejected; existing archive kept: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {args.format.upper()} data to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
