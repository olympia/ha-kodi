"""Check the test coverage of every module of the integration.

Run it after pytest wrote coverage.json, see the CI workflow. Every module
needs 95%, the config flow 100%. The modules below start lower, as taken over
from core. Their minimum is the coverage they have now, so it can only go up.
"""

import json
from pathlib import Path
import sys

DEFAULT_MINIMUM = 95.0
MINIMUM = {
    "custom_components/kodi/browse_media.py": 22.0,
    "custom_components/kodi/config_flow.py": 100.0,
    "custom_components/kodi/media_player.py": 69.9,
    "custom_components/kodi/notify.py": 92.3,
}


def main() -> int:
    """Report every module below its minimum."""
    report = json.loads(Path("coverage.json").read_text(encoding="utf-8"))
    measured = {
        name: data["summary"]["percent_covered"]
        for name, data in report["files"].items()
    }
    failed = False
    for name in sorted(measured.keys() | MINIMUM.keys()):
        if name not in measured:
            print(f"{name}: no coverage measured")
            failed = True
            continue
        minimum = MINIMUM.get(name, DEFAULT_MINIMUM)
        if measured[name] < minimum:
            print(f"{name}: {measured[name]:.1f}% is below {minimum:.1f}%")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
