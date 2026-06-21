"""Small local test harness for ChaseOS lifecycle health probes.

This exists to isolate health-contract behavior without relying on the
promoted top-level command surface or Discord-driven exec sessions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from runtime.lifecycle.health_cli import check_health  # type: ignore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a direct lifecycle health probe test")
    parser.add_argument("runtime_id", help="Runtime id to test")
    parser.add_argument("--timeout", type=int, default=5, help="Probe timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    result = check_health(args.runtime_id, timeout_seconds=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"runtime_id={result['runtime_id']}")
        print(f"healthy={result['healthy']}")
        print(f"timed_out={result['timed_out']}")
        print(f"kind={result.get('kind')}")
        if result.get('url'):
            print(f"url={result['url']}")
        if result.get('command'):
            print(f"command={result['command']}")
        if result.get('status_code') is not None:
            print(f"status_code={result['status_code']}")
        if result.get('returncode') is not None:
            print(f"returncode={result['returncode']}")
        if result.get('stderr'):
            print(f"stderr={result['stderr']}")
    return 0 if result.get("healthy") else 1


if __name__ == "__main__":
    raise SystemExit(main())
