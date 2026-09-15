from __future__ import annotations

import pytest

from receptionist.config import BusinessConfig, EspoCRMConfig
from receptionist.prompts import build_system_prompt


def test_espocrm_config_parses_basic_auth():
    config = EspoCRMConfig(
        enabled=True,
        base_url="http://localhost:8080/",
        account_name="Demo Clinic",
        auth={"type": "basic"},
    )
    assert config.base_url == "http://localhost:8080"
    assert config.auth.username_env == "ESPOCRM_USERNAME"
    assert config.auth.password_env == "ESPOCRM_PASSWORD"


def test_espocrm_appointments_require_account_name():
    with pytest.raises(ValueError, match="account_name"):
        EspoCRMConfig(
            enabled=True,
            auth={"type": "api_key"},
            appointments_enabled=True,
        )


def test_espocrm_url_rejects_embedded_credentials():
    with pytest.raises(ValueError, match="cannot contain credentials"):
        EspoCRMConfig(
            enabled=True,
            base_url="https://user:secret@crm.example.test",
            account_name="Demo Clinic",
            auth={"type": "api_key"},
        )


def test_prompt_includes_crm_safety_workflow(v2_yaml):
    yaml_text = v2_yaml + """
crm:
  provider: espocrm
  enabled: true
  base_url: http://localhost:8080
  account_name: Demo Clinic
  knowledge_enabled: true
  appointments_enabled: true
  auth:
    type: api_key
    api_key_env: TEST_ESPO_KEY
"""
    config = BusinessConfig.from_yaml_string(yaml_text)
    prompt = build_system_prompt(config)
    assert "CRM KNOWLEDGE BASE" in prompt
    assert "find_appointments" in prompt
    assert "reschedule_appointment" in prompt
    assert "cancel_appointment" in prompt
    assert "explicit confirmation" in prompt
