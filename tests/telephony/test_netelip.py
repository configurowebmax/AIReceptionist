from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from livekit import api

from receptionist.telephony.netelip import (
    NetelipConfigError,
    NetelipPlan,
    provision_livekit,
)
from receptionist.telephony.setup_cli import main


def _plan(**overrides) -> NetelipPlan:
    values = {
        "number": "+576011234567",
        "sip_endpoint": "sip:demo.sip.livekit.cloud;transport=tcp",
        "business": "example-dental",
        "agent_name": "receptionist",
        "allowed_addresses": ("192.99.91.144",),
    }
    values.update(overrides)
    return NetelipPlan.create(**values)


def test_plan_normalizes_inputs_and_builds_expected_payloads():
    plan = _plan()

    assert plan.sip_endpoint == "demo.sip.livekit.cloud"
    assert plan.allowed_addresses == ("192.99.91.144/32",)
    assert plan.inbound_payload()["trunk"]["numbers"] == ["+576011234567"]

    dispatch = plan.dispatch_payload("ST_demo")["dispatch_rule"]
    assert dispatch["trunk_ids"] == ["ST_demo"]
    agent = dispatch["roomConfig"]["agents"][0]
    assert agent["agentName"] == "receptionist"
    assert json.loads(agent["metadata"]) == {"config": "example-dental"}


def test_plan_supports_sip_auth_without_ip_allowlist():
    plan = _plan(
        allowed_addresses=(),
        auth_username="netelip-user",
        auth_password="netelip-password",
    )

    trunk = plan.inbound_payload()["trunk"]
    assert "allowed_addresses" not in trunk
    assert trunk["auth_username"] == "netelip-user"
    assert trunk["auth_password"] == "netelip-password"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", "6011234567"),
        ("number", "+000000000"),
        ("sip_endpoint", "https://demo.sip.livekit.cloud"),
        ("sip_endpoint", "demo host"),
        ("business", "../../secret"),
        ("allowed_addresses", ()),
        ("allowed_addresses", ("not-an-ip",)),
    ],
)
def test_plan_rejects_unsafe_or_invalid_values(field, value):
    with pytest.raises(NetelipConfigError):
        _plan(**{field: value})


def test_plan_writes_private_operator_files(tmp_path):
    paths = _plan().write(tmp_path)

    assert {path.name for path in paths} == {
        "livekit-inbound-trunk.json",
        "livekit-dispatch-rule.template.json",
        "netelip-routing.json",
    }
    routing = json.loads((tmp_path / "netelip-routing.json").read_text("utf-8"))
    assert routing["destination"] == "demo.sip.livekit.cloud"
    assert routing["number_format"] == "+E.164"


def test_cli_plan_generates_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "netelip",
            "plan",
            "--number",
            "+576011234567",
            "--sip-endpoint",
            "demo.sip.livekit.cloud",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "secrets/netelip/livekit-inbound-trunk.json").exists()
    assert "Dry run" not in capsys.readouterr().out


def test_cli_apply_without_yes_is_a_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "netelip",
            "apply",
            "--number",
            "+576011234567",
            "--sip-endpoint",
            "demo.sip.livekit.cloud",
        ]
    )

    assert exit_code == 0
    assert "Dry run only" in capsys.readouterr().out


def test_cli_requires_number_and_endpoint(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NETELIP_DID", raising=False)
    monkeypatch.delenv("LIVEKIT_SIP_ENDPOINT", raising=False)

    assert main(["netelip", "plan"]) == 2
    assert "Provide --number" in capsys.readouterr().err


def test_cli_auth_mode_requires_both_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NETELIP_SIP_USERNAME", raising=False)
    monkeypatch.delenv("NETELIP_SIP_PASSWORD", raising=False)

    exit_code = main(
        [
            "netelip",
            "plan",
            "--number",
            "+576011234567",
            "--sip-endpoint",
            "demo.sip.livekit.cloud",
            "--security",
            "auth",
        ]
    )

    assert exit_code == 2
    assert "requires NETELIP_SIP_USERNAME" in capsys.readouterr().err


def test_cli_auto_mode_refuses_partial_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NETELIP_SIP_USERNAME", "only-a-user")
    monkeypatch.delenv("NETELIP_SIP_PASSWORD", raising=False)

    exit_code = main(
        [
            "netelip",
            "plan",
            "--number",
            "+576011234567",
            "--sip-endpoint",
            "demo.sip.livekit.cloud",
        ]
    )

    assert exit_code == 2
    assert "must be set together" in capsys.readouterr().err


class _FakeSip:
    def __init__(self):
        self.trunks = []
        self.dispatch_rules = []
        self.inbound_request = None
        self.dispatch_request = None

    async def list_sip_inbound_trunk(self, request):
        return SimpleNamespace(items=self.trunks)

    async def create_sip_inbound_trunk(self, request):
        self.inbound_request = request
        trunk = api.SIPInboundTrunkInfo()
        trunk.CopyFrom(request.trunk)
        trunk.sip_trunk_id = "ST_netelip"
        self.trunks.append(trunk)
        return trunk

    async def list_sip_dispatch_rule(self, request):
        return SimpleNamespace(items=self.dispatch_rules)

    async def create_sip_dispatch_rule(self, request):
        self.dispatch_request = request
        dispatch = api.SIPDispatchRuleInfo()
        dispatch.CopyFrom(request.dispatch_rule)
        dispatch.sip_dispatch_rule_id = "SDR_netelip"
        self.dispatch_rules.append(dispatch)
        return dispatch


class _FakeLiveKit:
    def __init__(self):
        self.sip = _FakeSip()
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_provision_creates_resources_then_reuses_them(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://demo.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "valid-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "valid-secret")
    client = _FakeLiveKit()
    received = {}

    def factory(**kwargs):
        received.update(kwargs)
        return client

    first = await provision_livekit(_plan(), livekit_factory=factory)
    second = await provision_livekit(_plan(), livekit_factory=factory)

    assert received["url"] == "wss://demo.livekit.cloud"
    assert first == first.__class__(
        trunk_id="ST_netelip",
        dispatch_rule_id="SDR_netelip",
        trunk_created=True,
        dispatch_created=True,
    )
    assert second.trunk_created is False
    assert second.dispatch_created is False
    assert client.sip.inbound_request.trunk.allowed_addresses == [
        "192.99.91.144/32"
    ]
    agent = client.sip.dispatch_request.dispatch_rule.room_config.agents[0]
    assert agent.agent_name == "receptionist"
    assert json.loads(agent.metadata) == {"config": "example-dental"}
    assert client.closed is True


@pytest.mark.asyncio
async def test_provision_refuses_placeholder_livekit_credentials(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://demo.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "your-api-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")

    with pytest.raises(NetelipConfigError, match="LIVEKIT_API_KEY"):
        await provision_livekit(_plan(), livekit_factory=lambda **kwargs: None)
