"""Container health: the main loop touches a heartbeat file after every pass that reached the database."""
from __future__ import annotations

import os
import sys
import time

HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE", "/tmp/meshca-worker.heartbeat")
MAX_AGE_S = float(os.environ.get("HEARTBEAT_MAX_AGE_S", "180"))


def main() -> int:
    try:
        age = time.time() - os.stat(HEARTBEAT_FILE).st_mtime
    except OSError:
        print("no heartbeat yet")
        return 1
    if age > MAX_AGE_S:
        print(f"heartbeat is {age:.0f} s old")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
