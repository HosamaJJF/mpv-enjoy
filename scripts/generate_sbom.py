#!/usr/bin/env python3
"""Generate a compact SPDX 2.3 JSON SBOM from the dependency lock."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Dict, List, Optional
from urllib.parse import quote
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def spdx_id(name: str) -> str:
    return "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-")


def package_from_spec(name: str, spec: Dict[str, str]) -> Dict[str, object]:
    return {
        "name": name,
        "SPDXID": spdx_id(name),
        "versionInfo": spec["version"],
        "downloadLocation": spec.get("url", spec.get("repository", "NOASSERTION")),
        "filesAnalyzed": False,
        "licenseConcluded": spec.get("license", "NOASSERTION"),
        "licenseDeclared": spec.get("license", "NOASSERTION"),
        "copyrightText": "NOASSERTION",
        "checksums": [{"algorithm": "SHA256", "checksumValue": spec["sha256"]}]
        if spec.get("sha256")
        else [],
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": "pkg:github/{}/{}".format(
                    spec.get("repository", "https://github.com/unknown/unknown")
                    .removeprefix("https://github.com/")
                    .split("/")[0],
                    spec.get("repository", "https://github.com/unknown/unknown").rstrip("/").split("/")[-1],
                ),
            }
        ]
        if spec.get("repository")
        else [],
    }


def _home_dependency_packages(
    inventory: Dict[str, object],
) -> List[Dict[str, object]]:
    packages: List[Dict[str, object]] = []
    seen = set()
    for raw_component in inventory.get("components", []):  # type: ignore[union-attr]
        component = dict(raw_component)
        ecosystem = str(component.get("ecosystem", "unknown"))
        name = str(component.get("name", "unknown"))
        version = str(component.get("version", "unknown"))
        identity = (ecosystem, name, version)
        if identity in seen:
            continue
        seen.add(identity)
        license_id = str(component.get("license") or "NOASSERTION")
        if license_id.upper() == "UNKNOWN":
            license_id = "NOASSERTION"
        source = str(component.get("source") or "")
        purl_ecosystem = "cargo" if ecosystem == "cargo" else "npm"
        purl = "pkg:{}/{}@{}".format(
            purl_ecosystem,
            quote(name, safe="/"),
            quote(version, safe=""),
        )
        package_name = "mpv-enjoy-home-{}-{}".format(ecosystem, name)
        packages.append(
            {
                "name": package_name,
                "SPDXID": spdx_id("{}-{}".format(package_name, version)),
                "versionInfo": version,
                "downloadLocation": source if source.startswith("http") else "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": license_id,
                "licenseDeclared": license_id,
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": purl,
                    }
                ],
            }
        )
    return packages


def build_sbom(
    lock: Dict[str, object],
    platform: str,
    home_inventory: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    project_version = str(lock["project_version"])
    project_id = "SPDXRef-mpv-enjoy"
    packages: List[Dict[str, object]] = [
        {
            "name": "mpv-enjoy",
            "SPDXID": project_id,
            "versionInfo": project_version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "MIT",
            "licenseDeclared": "MIT",
            "copyrightText": "Copyright (c) 2026 mpv-enjoy contributors",
        }
    ]
    relationships: List[Dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": project_id,
        }
    ]
    for name, raw_spec in lock["components"].items():  # type: ignore[union-attr]
        spec = dict(raw_spec)
        package = package_from_spec(name, spec)
        packages.append(package)
        if name != "yt_dlp_source":
            relationships.append(
                {
                    "spdxElementId": project_id,
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": str(package["SPDXID"]),
                }
            )
    asset_spec = dict(lock["platform_assets"][platform]["yt_dlp"])  # type: ignore[index]
    asset_spec["repository"] = "https://github.com/yt-dlp/yt-dlp"
    package = package_from_spec("yt-dlp-binary-" + platform, asset_spec)
    packages.append(package)
    relationships.append(
        {
            "spdxElementId": project_id,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": str(package["SPDXID"]),
        }
    )
    relationships.append(
        {
            "spdxElementId": str(package["SPDXID"]),
            "relationshipType": "GENERATED_FROM",
            "relatedSpdxElement": spdx_id("yt_dlp_source"),
        }
    )
    if home_inventory is not None:
        home_id = spdx_id("mpv_enjoy_home")
        for home_package in _home_dependency_packages(home_inventory):
            packages.append(home_package)
            relationships.append(
                {
                    "spdxElementId": home_id,
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": str(home_package["SPDXID"]),
                }
            )

    identity = "{}:{}:{}".format(project_version, platform, asset_spec["sha256"])
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "mpv-enjoy-{}-{}".format(project_version, platform),
        "documentNamespace": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
        "creationInfo": {
            "created": str(lock.get("generated_at", datetime.now(timezone.utc).isoformat())),
            "creators": ["Tool: mpv-enjoy/scripts/generate_sbom.py"],
        },
        "packages": packages,
        "relationships": relationships,
        "hasExtractedLicensingInfos": [
            {
                "licenseId": "LicenseRef-yt-dlp-bundled",
                "name": "Licenses bundled in the official yt-dlp executable",
                "extractedText": "See LICENSES/yt-dlp-THIRD_PARTY_LICENSES.txt when present and the corresponding yt-dlp source archive.",
                "seeAlsos": ["https://github.com/yt-dlp/yt-dlp#license"],
            }
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=PROJECT_ROOT / "dependencies.lock.json")
    parser.add_argument(
        "--platform", required=True, choices=["windows-x64", "macos-arm64", "macos-x64"]
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    with args.lock.open("r", encoding="utf-8") as handle:
        lock = json.load(handle)
    sbom = build_sbom(lock, args.platform)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(sbom, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
