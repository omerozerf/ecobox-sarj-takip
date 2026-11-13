"""Compact status snapshots for a collector that has no database (GitHub Actions).

One file per UTC day, one JSON line per poll. A line holds a timestamp and, per connector,
the three raw fields the state normalisation needs. Connector ids are not repeated on every
line: `connectors.json` is an append-only index and a line's values are positional in it.

    connectors.json      ["<connector id>", ...]
    2026-11-13.jsonl     {"t":"2026-11-13T14:35:02Z","r":[[1,3,0],[2,2,0],null,...]}

`r[i]` is `[availability, connectorChargingState, isUnderMaintenance]` of `connectors[i]`,
or null when that connector was missing from the response. A line may be shorter than the
current index: connectors appended later were not known yet.
"""

import json
import sys
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ecobox.api import HttpEcoboxApi

INDEX_FILE = "connectors.json"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
REQUEST_TIMEOUT_S = 20.0

RawRow = dict[str, Any]


def extend_index(index: list[str], rows: list[RawRow]) -> list[str]:
    """The index with unseen connector ids appended; existing positions never move."""
    known = set(index)
    return index + sorted({row["connectorId"] for row in rows} - known)


def encode_line(ts: datetime, index: list[str], rows: list[RawRow]) -> str:
    by_id = {row["connectorId"]: row for row in rows}
    values = [_values(by_id.get(connector_id)) for connector_id in index]
    return json.dumps({"t": ts.strftime(TIMESTAMP_FORMAT), "r": values}, separators=(",", ":"))


def _values(row: RawRow | None) -> list[int | None] | None:
    if row is None:  # a connector can vanish from the status list
        return None
    return [row["availability"], row["connectorChargingState"], int(row["isUnderMaintenance"])]


def decode_line(line: str, index: list[str]) -> tuple[datetime, list[RawRow]]:
    """Inverse of `encode_line`: the poll time and the raw rows that were present."""
    record = json.loads(line)
    ts = datetime.strptime(record["t"], TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    rows = [{"connectorId": connector_id, "availability": values[0],
             "connectorChargingState": values[1], "isUnderMaintenance": bool(values[2])}
            for connector_id, values in zip(index, record["r"]) if values is not None]
    return ts, rows


def append_snapshot(directory: Path, ts: datetime, rows: list[RawRow]) -> Path:
    """Append one poll to the day file of `ts` and grow the index. Returns the day file."""
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / INDEX_FILE
    stored = json.loads(index_path.read_text()) if index_path.exists() else []
    index = extend_index(stored, rows)
    if index != stored:
        index_path.write_text(json.dumps(index, indent=0) + "\n")
    day_file = directory / f"{ts:%Y-%m-%d}.jsonl"
    with day_file.open("a") as handle:
        handle.write(encode_line(ts, index, rows) + "\n")
    return day_file


def read_snapshots(directory: Path,
                   since: date | None = None) -> Iterator[tuple[datetime, list[RawRow]]]:
    """Every stored poll, oldest first; with `since`, only the UTC day files from that day on."""
    index = json.loads((directory / INDEX_FILE).read_text())
    for day_file in sorted(directory.glob("*.jsonl")):
        if since is not None and day_file.stem < since.isoformat():
            continue
        for line in day_file.read_text().splitlines():
            yield decode_line(line, index)


def main(argv: list[str]) -> None:
    directory = Path(argv[1])
    rows = HttpEcoboxApi(REQUEST_TIMEOUT_S).fetch_status_rows()
    day_file = append_snapshot(directory, datetime.now(timezone.utc), rows)
    print(f"{len(rows)} connectors appended to {day_file.name}")


if __name__ == "__main__":
    main(sys.argv)
