"""Small fail-closed authentication boundary for the hackathon live flow."""

from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass
from enum import StrEnum

from app.live.config import LiveSettings


class LiveRole(StrEnum):
    OPERATOR = "operator"
    APPROVER = "approver"


@dataclass(frozen=True, slots=True)
class LiveActor:
    tenant_id: str
    user_id: str
    role: LiveRole


class LiveAuthenticationError(RuntimeError):
    pass


class ConfiguredActorAuthenticator:
    """Map opaque bearer credentials to server-configured identities.

    The caller never supplies the actor ID recorded in approval or delivery
    events. Possession of a configured credential resolves to one fixed actor.
    """

    def __init__(self, settings: LiveSettings) -> None:
        self._settings = settings

    def authenticate(
        self,
        authorization_header: str | None,
        *,
        required_role: LiveRole,
    ) -> LiveActor:
        if required_role == LiveRole.OPERATOR:
            expected = self._settings.operator_access_token
            user_id = self._settings.operator_user_id
        else:
            expected = self._settings.approver_access_token
            user_id = self._settings.approver_user_id

        supplied_user_id, token = _authorization_credential(authorization_header)
        user_matches = supplied_user_id is None or secrets.compare_digest(
            supplied_user_id,
            user_id,
        )
        if not user_matches or not secrets.compare_digest(token, expected):
            raise LiveAuthenticationError("invalid live actor credential")
        return LiveActor(
            tenant_id=self._settings.tenant_id,
            user_id=user_id,
            role=required_role,
        )


def _authorization_credential(value: str | None) -> tuple[str | None, str]:
    if not value:
        raise LiveAuthenticationError("missing Authorization header")
    scheme, separator, credential = value.partition(" ")
    if separator != " " or not credential.strip():
        raise LiveAuthenticationError("invalid Authorization header")
    if scheme.lower() == "bearer":
        return None, credential.strip()
    if scheme.lower() != "basic":
        raise LiveAuthenticationError("Authorization must use Bearer or Basic")
    try:
        decoded = base64.b64decode(credential, validate=True).decode("utf-8")
        user_id, password = decoded.split(":", maxsplit=1)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise LiveAuthenticationError("invalid Basic authorization") from error
    if not user_id or not password:
        raise LiveAuthenticationError("invalid Basic authorization")
    return user_id, password


__all__ = [
    "ConfiguredActorAuthenticator",
    "LiveActor",
    "LiveAuthenticationError",
    "LiveRole",
]
