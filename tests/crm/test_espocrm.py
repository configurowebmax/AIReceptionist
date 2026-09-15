from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from receptionist.config import EspoCRMConfig
from receptionist.crm import EspoCRMClient


@pytest.fixture
def crm_config(monkeypatch):
    monkeypatch.setenv("TEST_ESPO_KEY", "test-api-key")
    return EspoCRMConfig(
        enabled=True,
        base_url="https://crm.example.test",
        account_name="Demo Clinic",
        auth={"type": "api_key", "api_key_env": "TEST_ESPO_KEY"},
    )


@pytest.mark.asyncio
async def test_list_records_uses_api_key_and_json_search_params(crm_config):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "test-api-key"
        search = json.loads(request.url.params["searchParams"])
        assert search["where"][0]["attribute"] == "status"
        assert search["select"] == ["id", "name"]
        return httpx.Response(200, json={"list": [{"id": "1", "name": "A"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = EspoCRMClient(crm_config, http_client=http_client)
        records = await client.list_records(
            "Meeting",
            where=[{"type": "equals", "attribute": "status", "value": "Planned"}],
            select=["id", "name"],
        )
    assert records == [{"id": "1", "name": "A"}]


@pytest.mark.asyncio
async def test_knowledge_search_ranks_spanish_topic_synonyms(crm_config):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"list": [
            {
                "id": "hours",
                "name": "Horarios",
                "bodyPlain": "Atendemos de lunes a viernes.",
            },
            {
                "id": "location",
                "name": "Ubicacion y parqueadero",
                "bodyPlain": "Estamos en Avenida Demo. Hay parqueadero.",
            },
        ]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = EspoCRMClient(crm_config, http_client=http_client)
        results = await client.search_knowledge(
            "Donde quedan y tienen parqueadero?"
        )
    assert results[0].id == "location"
    assert "Avenida Demo" in results[0].body


@pytest.mark.asyncio
async def test_find_contact_requires_name_and_phone_match(crm_config):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"list": [{
            "id": "contact-1",
            "name": "Ana Torres",
            "firstName": "Ana",
            "lastName": "Torres",
            "phoneNumber": "+1 202-555-0101",
            "emailAddress": "ana@example.test",
        }]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = EspoCRMClient(crm_config, http_client=http_client)
        match = await client.find_contact("Ana Torres", "+12025550101")
        mismatch = await client.find_contact("Otra Persona", "+12025550101")
    assert match is not None
    assert match.id == "contact-1"
    assert mismatch is None


@pytest.mark.asyncio
async def test_create_appointment_links_account_as_parent(crm_config):
    meeting_payload = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and path.endswith("/Meeting"):
            return httpx.Response(200, json={"list": []})
        if method == "GET" and path.endswith("/Contact"):
            return httpx.Response(200, json={"list": []})
        if method == "GET" and path.endswith("/Account"):
            return httpx.Response(
                200, json={"list": [{"id": "account-1", "name": "Demo Clinic"}]}
            )
        if method == "POST" and path.endswith("/Contact"):
            return httpx.Response(200, json={
                "id": "contact-1",
                "name": "Ana Torres",
                "phoneNumber": "+12025550101",
                "emailAddress": None,
            })
        if method == "GET" and path.endswith("/App/user"):
            return httpx.Response(200, json={
                "user": {"id": "user-1", "name": "API User", "userName": "api"}
            })
        if method == "POST" and path.endswith("/Meeting"):
            meeting_payload.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "meeting-1"})
        raise AssertionError(f"unexpected request: {method} {path}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = EspoCRMClient(crm_config, http_client=http_client)
        result = await client.create_appointment(
            caller_name="Ana Torres",
            callback_number="+12025550101",
            start=datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
            service="Limpieza",
        )
    assert result.event_id == "meeting-1"
    assert meeting_payload["parentId"] == "account-1"
    assert meeting_payload["parentType"] == "Account"
    assert meeting_payload["contactsIds"] == ["contact-1"]
    assert meeting_payload["status"] == "Planned"


@pytest.mark.asyncio
async def test_cancel_updates_status_instead_of_deleting(crm_config):
    update_payload = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"list": [{
                "id": "meeting-1",
                "name": "Control",
                "status": "Planned",
                "dateStart": "2026-10-05 14:00:00",
                "dateEnd": "2026-10-05 14:45:00",
                "description": "Original",
            }]})
        assert request.method == "PUT"
        update_payload.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "meeting-1",
            "name": "Control",
            "status": "Not Held",
            "dateStart": "2026-10-05 14:00:00",
            "dateEnd": "2026-10-05 14:45:00",
            "description": update_payload["description"],
        })

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = EspoCRMClient(crm_config, http_client=http_client)
        result = await client.cancel_appointment(
            appointment_id="meeting-1",
            contact_id="contact-1",
            reason="Viaje",
        )
    assert result.status == "Not Held"
    assert update_payload["status"] == "Not Held"
    assert "Viaje" in update_payload["description"]
