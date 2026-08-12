#!/usr/bin/env python3
"""Emit stable Home cache metadata and detect Home integration changes."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = PROJECT_ROOT / "dependencies.lock.json"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_home.py"
ZERO_SHA = "0" * 40


class MetadataError(RuntimeError):
    pass


def load_lock_bytes(data: bytes) -> Dict[str, object]:
    parsed = json.loads(data.decode("utf-8"))
    if parsed.get("schema_version") != 1:
        raise MetadataError("Unsupported dependency lock schema")
    return parsed


def load_current_lock() -> Dict[str, object]:
    return load_lock_bytes(LOCK_PATH.read_bytes())


def load_lock_at_revision(revision: str) -> Dict[str, object]:
    result = subprocess.run(
        ["git", "show", "{}:dependencies.lock.json".format(revision)],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
    )
    return load_lock_bytes(result.stdout)


def home_spec(lock: Dict[str, object]) -> Dict[str, str]:
    components = lock.get("components")
    if not isinstance(components, dict):
        raise MetadataError("Lock file has no components object")
    home = components.get("mpv_enjoy_home")
    if not isinstance(home, dict):
        raise MetadataError("Lock file has no mpv_enjoy_home component")
    required = ("version", "commit", "sha256")
    values = {key: str(home.get(key, "")) for key in required}
    if any(not value for value in values.values()):
        raise MetadataError("Home lock metadata is incomplete")
    return values


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_script_changed(base: str) -> bool:
    result = subprocess.run(
        ["git", "diff", "--quiet", base, "HEAD", "--", "scripts/build_home.py"],
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    if result.returncode not in (0, 1):
        raise MetadataError("Unable to compare build_home.py with {}".format(base))
    return result.returncode == 1


def home_changed(current: Dict[str, object], base: Optional[str]) -> bool:
    if not base or base == ZERO_SHA:
        return True
    try:
        previous = load_lock_at_revision(base)
    except (
        MetadataError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return True
    return home_spec(previous) != home_spec(current) or build_script_changed(base)


def metadata(base: Optional[str] = None) -> Dict[str, str]:
    lock = load_current_lock()
    home = home_spec(lock)
    project_version = str(lock.get("project_version", ""))
    if not project_version:
        raise MetadataError("Lock file has no project_version")
    return {
        "home_changed": "true" if home_changed(lock, base) else "false",
        "home_commit": home["commit"],
        "home_source_sha256": home["sha256"],
        "home_build_sha256": file_sha256(BUILD_SCRIPT),
        "project_version": project_version,
    }


def write_github_output(path: Path, values: Dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write("{}={}\n".format(key, value))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        values = metadata(args.base)
        if args.github_output is not None:
            write_github_output(args.github_output, values)
        print(json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True))
    except (MetadataError, OSError, subprocess.CalledProcessError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
