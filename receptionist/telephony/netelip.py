from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from livekit import api

DEFAULT_NETELIP_LATAM_ADDRESSES = ("192.99.91.144/32",)

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_BUSINESS_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_SIP_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)


class NetelipConfigError(ValueError):
    """Raised when a Netelip/LiveKit provisioning value is unsafe or invalid."""


@dataclass(frozen=True)
class NetelipPlan:
    number: str
    sip_endpoint: str
    business: str
    agent_name: str
    allowed_addresses: tuple[str, ...]
    auth_username: str | None
    auth_password: str | None
    trunk_name: str
    dispatch_name: str
    room_prefix: str

    @classmethod
    def create(
        cls,
        *,
        number: str,
        sip_endpoint: str,
        business: str = "example-dental",
        agent_name: str = "receptionist",
        allowed_addresses: tuple[str, ...] = DEFAULT_NETELIP_LATAM_ADDRESSES,
        auth_username: str | None = None,
        auth_password: str | None = None,
    ) -> "NetelipPlan":
        normalized_number = number.strip()
        if not _E164.fullmatch(normalized_number):
            raise NetelipConfigError(
                "Netelip DID must use E.164 format, for example +576011234567."
            )

        endpoint = _normalize_sip_endpoint(sip_endpoint)
        business = business.strip()
        if not _BUSINESS_SLUG.fullmatch(business):
            raise NetelipConfigError(
                "Business must contain only letters, numbers, dashes, or underscores."
            )

        agent_name = agent_name.strip()
        if not agent_name or len(agent_name) > 128:
            raise NetelipConfigError("Agent name must contain between 1 and 128 characters.")

        normalized_addresses = _normalize_networks(allowed_addresses)
        auth_username = (auth_username or "").strip() or None
        auth_password = (auth_password or "").strip() or None
        if bool(auth_username) != bool(auth_password):
            raise NetelipConfigError(
                "Netelip SIP username and password must be provided together."
            )
        if not normalized_addresses and not auth_username:
            raise NetelipConfigError(
                "Inbound security requires Netelip SIP credentials or an IP/CIDR."
            )
        safe_slug = business.lower()
        return cls(
            number=normalized_number,
            sip_endpoint=endpoint,
            business=business,
            agent_name=agent_name,
            allowed_addresses=normalized_addresses,
            auth_username=auth_username,
            auth_password=auth_password,
            trunk_name=f"netelip-{safe_slug}-inbound",
            dispatch_name=f"Netelip {business}",
            room_prefix=f"netelip-{safe_slug}-",
        )

    def inbound_payload(self) -> dict[str, object]:
        trunk: dict[str, object] = {
            "name": self.trunk_name,
            "numbers": [self.number],
        }
        if self.allowed_addresses:
            trunk["allowed_addresses"] = list(self.allowed_addresses)
        if self.auth_username and self.auth_password:
            trunk["auth_username"] = self.auth_username
            trunk["auth_password"] = self.auth_password
        return {"trunk": trunk}

    def dispatch_payload(self, trunk_id: str = "TRUNK_ID_AFTER_APPLY") -> dict[str, object]:
        metadata = json.dumps(
            {"config": self.business},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return {
            "dispatch_rule": {
                "name": self.dispatch_name,
                "trunk_ids": [trunk_id],
                "rule": {
                    "dispatchRuleIndividual": {
                        "roomPrefix": self.room_prefix,
                    }
                },
                "roomConfig": {
                    "agents": [
                        {
                            "agentName": self.agent_name,
                            "metadata": metadata,
                        }
                    ]
                },
            }
        }

    def netelip_routing_payload(self) -> dict[str, object]:
        return {
            "did": self.number,
            "destination": self.sip_endpoint,
            "transport": "TCP",
            "number_format": "+E.164",
            "notes": [
                "Associate the DID with the Netelip SIP Trunk.",
                "Route inbound calls to the LiveKit SIP endpoint.",
                "Confirm Netelip source IP ranges before production.",
            ],
        }

    def write(self, output_dir: Path) -> tuple[Path, Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        inbound_path = output_dir / "livekit-inbound-trunk.json"
        dispatch_path = output_dir / "livekit-dispatch-rule.template.json"
        routing_path = output_dir / "netelip-routing.json"
        _write_private_json(inbound_path, self.inbound_payload())
        _write_private_json(dispatch_path, self.dispatch_payload())
        _write_private_json(routing_path, self.netelip_routing_payload())
        return inbound_path, dispatch_path, routing_path


@dataclass(frozen=True)
class ProvisionResult:
    trunk_id: str
    dispatch_rule_id: str
    trunk_created: bool
    dispatch_created: bool


async def provision_livekit(
    plan: NetelipPlan,
    *,
    livekit_factory: Callable[..., api.LiveKitAPI] = api.LiveKitAPI,
) -> ProvisionResult:
    """Idempotently create the inbound trunk and dispatch rule in LiveKit Cloud."""
    url = _required_livekit_env("LIVEKIT_URL")
    api_key = _required_livekit_env("LIVEKIT_API_KEY")
    api_secret = _required_livekit_env("LIVEKIT_API_SECRET")
    client = livekit_factory(url=url, api_key=api_key, api_secret=api_secret)
    try:
        trunk, trunk_created = await _ensure_trunk(client, plan)
        dispatch, dispatch_created = await _ensure_dispatch(
            client, plan, trunk.sip_trunk_id
        )
        return ProvisionResult(
            trunk_id=trunk.sip_trunk_id,
            dispatch_rule_id=dispatch.sip_dispatch_rule_id,
            trunk_created=trunk_created,
            dispatch_created=dispatch_created,
        )
    finally:
        await client.aclose()


async def _ensure_trunk(client: api.LiveKitAPI, plan: NetelipPlan):
    response = await client.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
    existing = next((item for item in response.items if item.name == plan.trunk_name), None)
    if existing is not None:
        if plan.number not in existing.numbers:
            raise NetelipConfigError(
                f"LiveKit trunk {plan.trunk_name!r} already exists with a different DID."
            )
        if set(existing.allowed_addresses) != set(plan.allowed_addresses):
            raise NetelipConfigError(
                f"LiveKit trunk {plan.trunk_name!r} already exists with different "
                "allowed addresses. Review it in the LiveKit dashboard."
            )
        if plan.auth_username and existing.auth_username != plan.auth_username:
            raise NetelipConfigError(
                f"LiveKit trunk {plan.trunk_name!r} already exists with a different "
                "authentication username."
            )
        return existing, False

    request = api.CreateSIPInboundTrunkRequest(
        trunk=api.SIPInboundTrunkInfo(
            name=plan.trunk_name,
            numbers=[plan.number],
            allowed_addresses=list(plan.allowed_addresses),
            auth_username=plan.auth_username or "",
            auth_password=plan.auth_password or "",
        )
    )
    return await client.sip.create_sip_inbound_trunk(request), True


async def _ensure_dispatch(
    client: api.LiveKitAPI,
    plan: NetelipPlan,
    trunk_id: str,
):
    response = await client.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
    existing = next((item for item in response.items if item.name == plan.dispatch_name), None)
    if existing is not None:
        if trunk_id not in existing.trunk_ids:
            raise NetelipConfigError(
                f"LiveKit dispatch rule {plan.dispatch_name!r} exists for another trunk."
            )
        return existing, False

    metadata = json.dumps(
        {"config": plan.business},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    info = api.SIPDispatchRuleInfo(
        name=plan.dispatch_name,
        trunk_ids=[trunk_id],
        rule=api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                room_prefix=plan.room_prefix
            )
        ),
        room_config=api.RoomConfiguration(
            agents=[
                api.RoomAgentDispatch(
                    agent_name=plan.agent_name,
                    metadata=metadata,
                )
            ]
        ),
    )
    request = api.CreateSIPDispatchRuleRequest(dispatch_rule=info)
    return await client.sip.create_sip_dispatch_rule(request), True


def _normalize_sip_endpoint(value: str) -> str:
    endpoint = value.strip()
    if endpoint.lower().startswith("sip:"):
        endpoint = endpoint[4:]
    endpoint = endpoint.split(";", 1)[0].rstrip(".")
    if "@" in endpoint:
        endpoint = endpoint.rsplit("@", 1)[1]
    if not _SIP_HOST.fullmatch(endpoint):
        raise NetelipConfigError(
            "LiveKit SIP endpoint must be a hostname such as "
            "project-id.sip.livekit.cloud."
        )
    return endpoint.lower()


def _normalize_networks(values: tuple[str, ...]) -> tuple[str, ...]:
    networks: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            raise NetelipConfigError(
                f"Invalid Netelip source IP/CIDR: {value!r}."
            ) from exc
        rendered = str(network)
        if rendered not in networks:
            networks.append(rendered)
    return tuple(networks)


def _required_livekit_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    placeholders = ("your-", "your_", "replace", "placeholder", "example")
    if not value or any(token in value.lower() for token in placeholders):
        raise NetelipConfigError(
            f"{name} is missing or still contains a placeholder in .env."
        )
    return value


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
