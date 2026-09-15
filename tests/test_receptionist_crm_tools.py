from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from receptionist.agent import Receptionist
from receptionist.config import BusinessConfig
from receptionist.crm import CRMAppointment, CRMContact, CRMKnowledgeResult
from receptionist.lifecycle import CallLifecycle


@pytest.fixture
def crm_receptionist(v2_yaml):
    yaml_text = v2_yaml + """
crm:
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
    lifecycle = CallLifecycle(
        config=config,
        call_id="crm-tools-test",
        caller_phone="+12025550101",
    )
    return Receptionist(config, lifecycle), lifecycle


@pytest.mark.asyncio
async def test_lookup_faq_falls_back_to_crm(crm_receptionist):
    receptionist, lifecycle = crm_receptionist
    receptionist._crm_client = SimpleNamespace(
        search_knowledge=AsyncMock(return_value=[
            CRMKnowledgeResult(
                id="kb-1",
                title="Seguros",
                body="Aceptamos el seguro demo.",
                score=4,
            )
        ])
    )
    result = await receptionist.lookup_faq(
        SimpleNamespace(), "Que seguros aceptan?"
    )
    assert "seguro demo" in result
    assert lifecycle.metadata.faqs_answered == ["Seguros"]


@pytest.mark.asyncio
async def test_find_appointments_returns_real_crm_id(crm_receptionist):
    receptionist, _ = crm_receptionist
    start = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
    fake = SimpleNamespace(
        find_contact=AsyncMock(return_value=CRMContact(
            id="contact-1",
            name="Ana Torres",
            phone_number="+12025550101",
            email_address=None,
        )),
        appointments_for_contact=AsyncMock(return_value=[
            CRMAppointment(
                id="meeting-1",
                name="Limpieza",
                status="Planned",
                start=start,
                end=start + timedelta(minutes=45),
                description="",
            )
        ]),
    )
    receptionist._crm_client = fake
    result = await receptionist.find_appointments(
        SimpleNamespace(), "Ana Torres", "+12025550101"
    )
    assert "meeting-1" in result
    assert "Limpieza" in result


@pytest.mark.asyncio
async def test_cancel_requires_explicit_confirmation(crm_receptionist):
    receptionist, lifecycle = crm_receptionist
    fake = SimpleNamespace(
        find_contact=AsyncMock(),
        cancel_appointment=AsyncMock(),
    )
    receptionist._crm_client = fake
    result = await receptionist.cancel_appointment(
        SimpleNamespace(),
        "meeting-1",
        "Ana Torres",
        "+12025550101",
        False,
        "Cambio de planes",
    )
    assert "Do not cancel yet" in result
    fake.find_contact.assert_not_awaited()
    fake.cancel_appointment.assert_not_awaited()
    assert "appointment_cancelled" not in lifecycle.metadata.outcomes


@pytest.mark.asyncio
async def test_reschedule_requires_offered_slot(crm_receptionist):
    receptionist, lifecycle = crm_receptionist
    fake = SimpleNamespace(
        find_contact=AsyncMock(),
        reschedule_appointment=AsyncMock(),
    )
    receptionist._crm_client = fake
    result = await receptionist.reschedule_appointment(
        SimpleNamespace(),
        "meeting-1",
        "Ana Torres",
        "+12025550101",
        "2026-10-05T10:00:00-05:00",
        True,
    )
    assert "check_availability" in result
    fake.find_contact.assert_not_awaited()
    assert "appointment_rescheduled" not in lifecycle.metadata.outcomes
