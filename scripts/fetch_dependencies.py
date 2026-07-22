#!/usr/bin/env python3
"""Fetch and safely extract artifacts declared in dependencies.lock.json."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = PROJECT_ROOT / "dependencies.lock.json"
DEFAULT_CACHE = PROJECT_ROOT / ".cache" / "downloads"


class DependencyError(RuntimeError):
    pass


def load_lock(path: Path = DEFAULT_LOCK) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        lock = json.load(handle)
    if lock.get("schema_version") != 1:
        raise DependencyError("Unsupported dependency lock schema")
    return lock


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected: str) -> bool:
    return path.is_file() and sha256_file(path) == expected.lower()


def download_artifact(name: str, spec: Dict[str, str], cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / spec["filename"]
    expected = spec["sha256"].lower()
    if verify_file(destination, expected):
        return destination
    if destination.exists():
        destination.unlink()

    request = Request(
        spec["url"],
        headers={"User-Agent": "mpv-lazy-enjoy-dependency-fetcher/0.1"},
    )
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=destination.name + ".", suffix=".part", dir=str(cache_dir), delete=False
        ) as output:
            temporary = Path(output.name)
            with urlopen(request, timeout=120) as response:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        actual = sha256_file(temporary)
        if actual != expected:
            raise DependencyError(
                "SHA-256 mismatch for {}: expected {}, got {}".format(name, expected, actual)
            )
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def _resolved_member_path(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise DependencyError("Archive member escapes extraction root: {}".format(name)) from error
    return candidate


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(archive), mode="r:*") as bundle:
        for member in bundle.getmembers():
            member_path = _resolved_member_path(destination, member.name)
            if member.isdev() or member.isfifo():
                raise DependencyError("Unsupported special archive member: {}".format(member.name))
            if member.issym() or member.islnk():
                link_target = Path(member.linkname)
                if link_target.is_absolute():
                    raise DependencyError("Absolute archive link is not allowed: {}".format(member.name))
                _resolved_member_path(member_path.parent, member.linkname)
        bundle.extractall(str(destination))


def _assert_safe_replace_target(path: Path) -> None:
    resolved = path.resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), PROJECT_ROOT.resolve()}
    if resolved in forbidden or len(resolved.parts) < 3:
        raise DependencyError("Refusing to replace unsafe extraction target: {}".format(path))


def extract_component(
    name: str,
    spec: Dict[str, str],
    cache_dir: Path,
    destination: Path,
    force: bool = False,
) -> Path:
    archive = download_artifact(name, spec, cache_dir)
    if destination.exists():
        if not force:
            raise DependencyError("Extraction destination already exists: {}".format(destination))
        _assert_safe_replace_target(destination)
        shutil.rmtree(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mpv-lazy-enjoy-extract-") as temporary:
        temporary_root = Path(temporary)
        safe_extract_tar(archive, temporary_root)
        entries = list(temporary_root.iterdir())
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else temporary_root
        shutil.copytree(str(source), str(destination))
    return destination


def component_specs(lock: Dict[str, object]) -> Dict[str, Dict[str, str]]:
    return dict(lock["components"])  # type: ignore[arg-type]


def platform_asset_spec(lock: Dict[str, object], platform: str) -> Dict[str, str]:
    platforms = lock["platform_assets"]  # type: ignore[index]
    if platform not in platforms:
        raise DependencyError("Unknown platform: {}".format(platform))
    return dict(platforms[platform]["yt_dlp"])


def parse_extract(value: str) -> Tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--extract must be COMPONENT=PATH")
    name, path = value.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("--extract must be COMPONENT=PATH")
    return name, Path(path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--component", action="append", default=[])
    parser.add_argument("--platform", choices=["windows-x64", "macos-arm64"])
    parser.add_argument("--all", action="store_true", help="Fetch every source component")
    parser.add_argument("--extract", action="append", default=[], type=parse_extract)
    parser.add_argument("--force-extract", action="store_true")
    args = parser.parse_args(argv)

    lock = load_lock(args.lock)
    components = component_specs(lock)
    selected: Iterable[str] = components.keys() if args.all else args.component
    fetched: Dict[str, str] = {}
    try:
        for name in selected:
            if name not in components:
                raise DependencyError("Unknown component: {}".format(name))
            path = download_artifact(name, components[name], args.cache)
            fetched[name] = str(path)
        if args.platform:
            spec = platform_asset_spec(lock, args.platform)
            path = download_artifact("yt_dlp@" + args.platform, spec, args.cache)
            fetched["yt_dlp@" + args.platform] = str(path)
        for name, destination in args.extract:
            if name not in components:
                raise DependencyError("Unknown extract component: {}".format(name))
            path = extract_component(
                name, components[name], args.cache, destination, force=args.force_extract
            )
            fetched["extract:" + name] = str(path)
    except (DependencyError, OSError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1

    print(json.dumps(fetched, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
