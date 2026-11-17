"""Put the integration and its tests into a Home Assistant Core checkout.

The pylint plugins and the quality scale checks of core only work on the
layout of core. This script copies the integration and its tests to where they
would be in core, undoing the differences listed in README.md, so the checks
of core can run on them. Run ruff --fix on the tests in core afterwards, to
sort the imports again.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import sys

DOMAIN = "kodi"
ROOT = Path(__file__).parent.parent
CUSTOM_ONLY = ("brand", "translations")
CUSTOM_MANIFEST_KEYS = ("issue_tracker", "version")
CORE_MANIFEST_KEYS = ("codeowners", "documentation")


def pinned(requirement: str) -> str:
    """Return a minimum version requirement as the exact pin core uses."""
    return requirement.replace(">=", "==", 1)


def core_manifest(manifest: str, core: str) -> str:
    """Return the manifest the way core has it."""
    core_values = json.loads(core)
    values = {key: core_values[key] for key in CORE_MANIFEST_KEYS}
    values["requirements"] = [
        pinned(requirement) for requirement in json.loads(manifest)["requirements"]
    ]
    lines = [
        line
        for line in manifest.splitlines(keepends=True)
        if not any(
            line.lstrip().startswith(f'"{key}":') for key in CUSTOM_MANIFEST_KEYS
        )
    ]
    text = "".join(lines)
    for key, value in values.items():
        if match := re.search(rf'^(\s*"{key}": ).*?(,?)$', text, re.MULTILINE):
            text = f"{text[: match.start()]}{match[1]}{json.dumps(value)}{match[2]}{text[match.end() :]}"
    return text


def main() -> int:
    """Copy the integration and its tests into the core checkout."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("core", type=Path, help="Path of the core checkout")
    args = parser.parse_args()

    integration = args.core / "homeassistant" / "components" / DOMAIN
    tests = args.core / "tests" / "components" / DOMAIN
    if not (integration / "manifest.json").is_file():
        print(f"{integration} is not the Kodi integration of a core checkout")
        return 1

    core = (integration / "manifest.json").read_text(encoding="utf-8")
    shutil.rmtree(integration)
    shutil.copytree(
        ROOT / "custom_components" / DOMAIN,
        integration,
        ignore=shutil.ignore_patterns(*CUSTOM_ONLY, "__pycache__"),
    )
    manifest = integration / "manifest.json"
    manifest.write_text(
        core_manifest(manifest.read_text(encoding="utf-8"), core), encoding="utf-8"
    )

    shutil.rmtree(tests)
    shutil.copytree(
        ROOT / "tests" / "components" / DOMAIN,
        tests,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for path in tests.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                f"custom_components.{DOMAIN}", f"homeassistant.components.{DOMAIN}"
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
