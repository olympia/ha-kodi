"""Generate the translations of the integration from its strings.json.

Home Assistant only compiles strings.json for integrations in core. This
script does the same for this repository: en.json gets strings.json with every
reference replaced by its text. The other languages are taken from the Kodi
translations of the installed Home Assistant, but only for keys whose English
text is the same here, so a changed or new key falls back to English.
"""

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

import homeassistant

DOMAIN = "kodi"
INTEGRATION = Path(__file__).parent.parent / "custom_components" / DOMAIN
HA_ROOT = Path(homeassistant.__file__).parent
REFERENCE = re.compile(r"\[%key:([a-z0-9_]+(?:::(?:[a-z0-9-_])+)+)%\]")


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from a file."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Return the strings of a translation tree by their key path."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}::{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def unflatten(flat: dict[str, str]) -> dict[str, Any]:
    """Return the translation tree of strings keyed by their path."""
    data: dict[str, Any] = {}
    for path, value in flat.items():
        *parents, key = path.split("::")
        node = data
        for parent in parents:
            node = node.setdefault(parent, {})
        node[key] = value
    return data


def resolve(value: str, lookup: dict[str, str], seen: tuple[str, ...] = ()) -> str:
    """Replace the references in a string, also those in the referenced text."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in seen:
            raise ValueError(f"Circular reference to {key}")
        if key not in lookup:
            raise ValueError(f"Unknown reference to {key}")
        return resolve(lookup[key], lookup, (*seen, key))

    return REFERENCE.sub(replace, value)


def generate() -> dict[str, dict[str, Any]]:
    """Return the content of every translation file by its name."""
    strings = flatten(load_json(INTEGRATION / "strings.json"))
    lookup = flatten(load_json(HA_ROOT / "strings.json"))
    lookup.update(
        {f"component::{DOMAIN}::{key}": text for key, text in strings.items()}
    )
    english = {key: resolve(text, lookup) for key, text in strings.items()}

    files = {"en.json": unflatten(english)}
    core_dir = HA_ROOT / "components" / DOMAIN / "translations"
    core_english = flatten(load_json(core_dir / "en.json"))
    for path in sorted(core_dir.glob("*.json")):
        if path.name == "en.json":
            continue
        kept = {
            key: text
            for key, text in flatten(load_json(path)).items()
            if key in english and core_english.get(key) == english[key]
        }
        if kept:
            files[path.name] = unflatten(kept)
    return files


def render(data: dict[str, Any]) -> str:
    """Return a translation file in the format of the files core ships."""
    return json.dumps(data, sort_keys=True, indent=4)


def main() -> int:
    """Write the translations, or check them with --check."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report the files that are not up to date",
    )
    args = parser.parse_args()

    target = INTEGRATION / "translations"
    files = generate()
    stale = sorted(
        {path.name for path in target.glob("*.json")} - files.keys()
        if target.is_dir()
        else set()
    )
    changed = [
        name
        for name, data in files.items()
        if not (target / name).is_file()
        or (target / name).read_text(encoding="utf-8") != render(data)
    ]

    if args.check:
        for name in [*changed, *stale]:
            print(f"translations/{name} is not up to date")
        return 1 if changed or stale else 0

    target.mkdir(exist_ok=True)
    for name in changed:
        (target / name).write_text(render(files[name]), encoding="utf-8")
    for name in stale:
        (target / name).unlink()
    print(f"Wrote {len(changed)} and removed {len(stale)} translation files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
