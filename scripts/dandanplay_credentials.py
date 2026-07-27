#!/usr/bin/env python3
"""Validate and identify encrypted dandanplay application credentials."""

import base64
import binascii
import hashlib
from typing import Mapping, NamedTuple


APP_ID_ENV = "MPV_ENJOY_DANDANPLAY_APP_ID_AES_B64"
APP_SECRET_ENV = "MPV_ENJOY_DANDANPLAY_APP_SECRET_AES_B64"

# These values belong to the pinned upstream uosc_danmaku source and are only
# used as strict patch anchors. Release builds must replace both of them.
UPSTREAM_APP_ID_AES_B64 = "UgjRIH45lE1BBLNmir1WKw=="
UPSTREAM_APP_SECRET_AES_B64 = "SzuWlFZAPRMqeWf9qmfp8dcvYr3hvxuSrIRZuAeEfko="

APP_ID_PREFIX = '        local appid = "'
APP_SECRET_PREFIX = '        local app_accept = "'
ASSIGNMENT_SUFFIX = '"'


class DandanplayCredentialError(ValueError):
    pass


class DandanplayCredentials(NamedTuple):
    app_id_aes_b64: str
    app_secret_aes_b64: str


def _validate_ciphertext(name: str, value: str) -> str:
    if not value:
        raise DandanplayCredentialError("Missing required environment variable: " + name)
    if value != value.strip() or not value.isascii():
        raise DandanplayCredentialError(name + " must be ASCII without surrounding whitespace")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise DandanplayCredentialError(name + " must be canonical Base64") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise DandanplayCredentialError(name + " must be canonical Base64")
    if not decoded or len(decoded) % 16 != 0 or len(decoded) > 128:
        raise DandanplayCredentialError(
            name + " must decode to between 16 and 128 bytes in complete AES blocks"
        )
    return value


def load_credentials(environment: Mapping[str, str]) -> DandanplayCredentials:
    credentials = DandanplayCredentials(
        _validate_ciphertext(APP_ID_ENV, environment.get(APP_ID_ENV, "")),
        _validate_ciphertext(APP_SECRET_ENV, environment.get(APP_SECRET_ENV, "")),
    )
    if credentials.app_id_aes_b64 == UPSTREAM_APP_ID_AES_B64:
        raise DandanplayCredentialError(APP_ID_ENV + " still contains the upstream credential")
    if credentials.app_secret_aes_b64 == UPSTREAM_APP_SECRET_AES_B64:
        raise DandanplayCredentialError(APP_SECRET_ENV + " still contains the upstream credential")
    return credentials


def app_id_assignment(value: str) -> str:
    return APP_ID_PREFIX + value + ASSIGNMENT_SUFFIX


def app_secret_assignment(value: str) -> str:
    return APP_SECRET_PREFIX + value + ASSIGNMENT_SUFFIX


def upstream_app_id_assignment() -> str:
    return app_id_assignment(UPSTREAM_APP_ID_AES_B64)


def upstream_app_secret_assignment() -> str:
    return app_secret_assignment(UPSTREAM_APP_SECRET_AES_B64)


def credential_fingerprint(credentials: DandanplayCredentials) -> str:
    payload = (
        credentials.app_id_aes_b64 + "\0" + credentials.app_secret_aes_b64
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def verify_patched_lua(text: str, credentials: DandanplayCredentials) -> None:
    if text.count(APP_ID_PREFIX) != 1 or text.count(APP_SECRET_PREFIX) != 1:
        raise DandanplayCredentialError(
            "dandanplay credential assignments are missing or ambiguous"
        )
    expected = (
        app_id_assignment(credentials.app_id_aes_b64),
        app_secret_assignment(credentials.app_secret_aes_b64),
    )
    upstream = (
        upstream_app_id_assignment(),
        upstream_app_secret_assignment(),
    )
    for assignment in expected:
        if text.count(assignment) != 1:
            raise DandanplayCredentialError(
                "Assembled dandanplay credentials do not match the build inputs"
            )
    for assignment in upstream:
        if assignment in text:
            raise DandanplayCredentialError(
                "Assembled dandanplay API still contains an upstream credential"
            )
