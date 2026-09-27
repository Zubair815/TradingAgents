"""Content-addressed, immutable snapshots for calendar and news observations."""

import hashlib
import json
from pathlib import Path


def save_snapshot(directory, snapshot):
    raw = json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.json"
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        pass
    return path


def read_snapshots(directory):
    for path in Path(directory).glob("*.json"):
        try:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() == path.stem:
                snapshot = json.loads(raw)
                if isinstance(snapshot, dict):
                    yield snapshot
        except (OSError, ValueError):
            continue
