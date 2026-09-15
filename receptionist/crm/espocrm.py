from __future__ import annotations

import base64
import html
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from receptionist.booking.models import BookingResult
from receptionist.config import (
    EspoCRMAPIKeyAuth,
    EspoCRMBasicAuth,
    EspoCRMConfig,
)


class CRMRequestError(RuntimeError):
    """Raised when EspoCRM cannot complete an API request."""


class CRMNotFoundError(RuntimeError):
    """Raised when a configured CRM record cannot be found."""


class CRMIdentityError(RuntimeError):
    """Raised when caller-provided identity data does not match a contact."""


class CRMSlotUnavailableError(RuntimeError):
    """Raised when a CRM appointment slot overlaps another planned meeting."""


@dataclass(frozen=True)
class CRMContact:
    id: str
    name: str
    phone_number: str | None
    email_address: str | None


@dataclass(frozen=True)
class CRMAppointment:
    id: str
    name: str
    status: str
    start: datetime
    end: datetime
    description: str


@dataclass(frozen=True)
class CRMKnowledgeResult:
    id: str
    title: str
    body: str
    score: int


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a", "al", "and", "como", "cual", "de", "del", "do", "el", "en",
    "es", "esta", "estan", "hay", "la", "las", "los", "me", "of", "para",
    "por", "que", "se", "the", "un", "una", "y",
}
_TOPIC_SYNONYMS = {
    "horario": {"abren", "atienden", "atencion", "cierran", "hora"},
    "seguro": {"aseguradora", "cobertura", "pago", "pagos"},
    "ubicacion": {"direccion", "donde", "llegar", "parqueadero", "transporte"},
    "agendamiento": {"agendar", "cita", "reservar"},
    "reprogramacion": {"cambiar", "mover", "reprogramar"},
    "cancelacion": {"anular", "cancelar"},
    "urgencia": {"dolor", "emergencia", "fiebre", "sangrado"},
}


def _env_required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise CRMRequestError(f"CRM environment variable {name!r} is not set")
    return value


def _normal_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(_WORD_RE.findall(ascii_text.lower()))


def _normal_phone(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _parse_espo_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )


def _format_espo_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("CRM datetimes must include a timezone")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _appointment_from_record(record: dict[str, Any]) -> CRMAppointment:
    return CRMAppointment(
        id=record["id"],
        name=record.get("name") or "Appointment",
        status=record.get("status") or "",
        start=_parse_espo_datetime(record["dateStart"]),
        end=_parse_espo_datetime(record["dateEnd"]),
        description=record.get("description") or "",
    )


def _contact_from_record(record: dict[str, Any]) -> CRMContact:
    return CRMContact(
        id=record["id"],
        name=record.get("name")
        or " ".join(
            part for part in (
                record.get("firstName"), record.get("lastName")
            ) if part
        ),
        phone_number=record.get("phoneNumber"),
        email_address=record.get("emailAddress"),
    )


class EspoCRMClient:
    """Small async client exposing only receptionist-safe EspoCRM operations."""

    def __init__(
        self,
        config: EspoCRMConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._http_client = http_client
        self._account: dict[str, Any] | None = None
        self._user: dict[str, Any] | None = None

        if isinstance(config.auth, EspoCRMAPIKeyAuth):
            self._headers = {
                "X-Api-Key": _env_required(config.auth.api_key_env),
            }
        elif isinstance(config.auth, EspoCRMBasicAuth):
            username = _env_required(config.auth.username_env)
            password = _env_required(config.auth.password_env)
            token = base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")
            self._headers = {"Espo-Authorization": token}
        else:  # pragma: no cover - protected by Pydantic's discriminator
            raise CRMRequestError("Unsupported EspoCRM authentication type")

        self._headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url}/api/v1/{path.lstrip('/')}"
        try:
            if self._http_client is not None:
                response = await self._http_client.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    headers=self._headers,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.config.request_timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=payload,
                        headers=self._headers,
                    )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise CRMRequestError(
                f"EspoCRM {method} {path} failed with HTTP "
                f"{exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise CRMRequestError(
                f"EspoCRM {method} {path} request failed"
            ) from exc
        if not response.content:
            return {}
        try:
            result = response.json()
        except ValueError as exc:
            raise CRMRequestError(
                f"EspoCRM {method} {path} returned invalid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise CRMRequestError(
                f"EspoCRM {method} {path} returned an unexpected response"
            )
        return result

    async def list_records(
        self,
        entity: str,
        *,
        where: list[dict[str, Any]] | None = None,
        select: list[str] | None = None,
        max_size: int = 50,
        order_by: str | None = None,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        search: dict[str, Any] = {"maxSize": max_size}
        if where:
            search["where"] = where
        if select:
            search["select"] = select
        if order_by:
            search["orderBy"] = order_by
            search["order"] = order
        result = await self._request(
            "GET",
            entity,
            params={
                "searchParams": json.dumps(
                    search, ensure_ascii=False, separators=(",", ":")
                )
            },
        )
        records = result.get("list", [])
        if not isinstance(records, list):
            raise CRMRequestError(f"EspoCRM {entity} list is malformed")
        return records

    async def health(self) -> str:
        result = await self._request("GET", "App/user")
        user = result.get("user") or {}
        return user.get("userName") or user.get("name") or "authenticated"

    async def _current_user(self) -> dict[str, Any]:
        if self._user is None:
            result = await self._request("GET", "App/user")
            user = result.get("user")
            if not isinstance(user, dict) or not user.get("id"):
                raise CRMRequestError("EspoCRM App/user response is missing user data")
            self._user = user
        return self._user

    async def _business_account(self) -> dict[str, Any]:
        if self._account is not None:
            return self._account
        records = await self.list_records(
            "Account",
            where=[{
                "type": "equals",
                "attribute": "name",
                "value": self.config.account_name,
            }],
            select=["id", "name"],
            max_size=2,
        )
        if not records:
            raise CRMNotFoundError(
                f"CRM account {self.config.account_name!r} was not found"
            )
        if len(records) > 1:
            raise CRMRequestError(
                f"CRM account name {self.config.account_name!r} is not unique"
            )
        self._account = records[0]
        return self._account

    async def _contacts_for_phone(
        self,
        callback_number: str,
    ) -> list[dict[str, Any]]:
        normalized = _normal_phone(callback_number)
        if len(normalized) < 7:
            raise CRMIdentityError("A valid callback number is required")
        records = await self.list_records(
            "Contact",
            where=[{
                "type": "contains",
                "attribute": "phoneNumber",
                "value": normalized[-7:],
            }],
            select=[
                "id", "name", "firstName", "lastName",
                "phoneNumber", "emailAddress",
            ],
            max_size=10,
        )
        return [
            record for record in records
            if _normal_phone(record.get("phoneNumber")) == normalized
            or _normal_phone(record.get("phoneNumber")).endswith(normalized[-7:])
        ]

    async def find_contact(
        self,
        caller_name: str,
        callback_number: str,
    ) -> CRMContact | None:
        expected_name = _normal_text(caller_name)
        if len(expected_name) < 3:
            raise CRMIdentityError("A full caller name is required")
        matches = [
            record
            for record in await self._contacts_for_phone(callback_number)
            if _normal_text(record.get("name") or "") == expected_name
        ]
        if len(matches) > 1:
            raise CRMIdentityError(
                "Multiple CRM contacts match that name and callback number"
            )
        return _contact_from_record(matches[0]) if matches else None

    async def ensure_contact(
        self,
        caller_name: str,
        callback_number: str,
        caller_email: str | None = None,
    ) -> CRMContact:
        existing_for_phone = await self._contacts_for_phone(callback_number)
        expected_name = _normal_text(caller_name)
        matching = [
            record for record in existing_for_phone
            if _normal_text(record.get("name") or "") == expected_name
        ]
        if matching:
            return _contact_from_record(matching[0])
        if existing_for_phone:
            raise CRMIdentityError(
                "The callback number belongs to a different CRM contact"
            )

        parts = caller_name.strip().split()
        if len(parts) < 2:
            raise CRMIdentityError("A first and last name are required")
        account = await self._business_account()
        payload: dict[str, Any] = {
            "firstName": parts[0],
            "lastName": " ".join(parts[1:]),
            "phoneNumber": callback_number,
            "accountId": account["id"],
            "accountName": account["name"],
            "description": "[AI RECEPTIONIST] Contact created during booking.",
        }
        if caller_email:
            payload["emailAddress"] = caller_email
        return _contact_from_record(
            await self._request("POST", "Contact", payload=payload)
        )

    async def appointments_for_contact(
        self,
        contact_id: str,
        *,
        planned_only: bool = True,
    ) -> list[CRMAppointment]:
        where: list[dict[str, Any]] = [{
            "type": "linkedWith",
            "attribute": "contacts",
            "value": [contact_id],
        }]
        if planned_only:
            where.append({
                "type": "equals",
                "attribute": "status",
                "value": "Planned",
            })
        records = await self.list_records(
            "Meeting",
            where=where,
            select=[
                "id", "name", "status", "dateStart", "dateEnd", "description",
            ],
            max_size=20,
            order_by="dateStart",
        )
        return [_appointment_from_record(record) for record in records]

    async def appointment_for_contact(
        self,
        appointment_id: str,
        contact_id: str,
    ) -> CRMAppointment:
        records = await self.list_records(
            "Meeting",
            where=[
                {
                    "type": "equals",
                    "attribute": "id",
                    "value": appointment_id,
                },
                {
                    "type": "linkedWith",
                    "attribute": "contacts",
                    "value": [contact_id],
                },
            ],
            select=[
                "id", "name", "status", "dateStart", "dateEnd", "description",
            ],
            max_size=2,
        )
        if not records:
            raise CRMNotFoundError(
                "Appointment was not found for the verified CRM contact"
            )
        return _appointment_from_record(records[0])

    async def busy_appointments(
        self,
        earliest: datetime,
        latest: datetime,
    ) -> list[CRMAppointment]:
        account = await self._business_account()
        records = await self.list_records(
            "Meeting",
            where=[
                {
                    "type": "equals",
                    "attribute": "parentId",
                    "value": account["id"],
                },
                {
                    "type": "equals",
                    "attribute": "parentType",
                    "value": "Account",
                },
                {
                    "type": "equals",
                    "attribute": "status",
                    "value": "Planned",
                },
                {
                    "type": "lessThan",
                    "attribute": "dateStart",
                    "value": _format_espo_datetime(latest),
                },
                {
                    "type": "greaterThan",
                    "attribute": "dateEnd",
                    "value": _format_espo_datetime(earliest),
                },
            ],
            select=[
                "id", "name", "status", "dateStart", "dateEnd", "description",
            ],
            max_size=200,
            order_by="dateStart",
        )
        return [_appointment_from_record(record) for record in records]

    async def busy_intervals(
        self,
        earliest: datetime,
        latest: datetime,
    ) -> list[tuple[datetime, datetime]]:
        return [
            (appointment.start, appointment.end)
            for appointment in await self.busy_appointments(earliest, latest)
        ]

    def _buffered(self, appointment: CRMAppointment) -> tuple[datetime, datetime]:
        buffer = timedelta(minutes=self.config.buffer_minutes)
        if self.config.buffer_placement == "before":
            return appointment.start - buffer, appointment.end
        if self.config.buffer_placement == "both":
            half = buffer / 2
            return appointment.start - half, appointment.end + half
        return appointment.start, appointment.end + buffer

    async def _assert_slot_available(
        self,
        start: datetime,
        end: datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> None:
        buffer = timedelta(minutes=self.config.buffer_minutes)
        for appointment in await self.busy_appointments(
            start - buffer, end + buffer
        ):
            if appointment.id == exclude_appointment_id:
                continue
            busy_start, busy_end = self._buffered(appointment)
            if start < busy_end and busy_start < end:
                raise CRMSlotUnavailableError(
                    "The requested appointment time is no longer available"
                )

    async def create_appointment(
        self,
        *,
        caller_name: str,
        callback_number: str,
        start: datetime,
        notes: str | None = None,
        caller_email: str | None = None,
        service: str = "Appointment",
    ) -> BookingResult:
        duration = timedelta(minutes=self.config.appointment_duration_minutes)
        end = start + duration
        await self._assert_slot_available(start, end)
        contact = await self.ensure_contact(
            caller_name, callback_number, caller_email
        )
        account = await self._business_account()
        user = await self._current_user()
        description_lines = [
            "[AI RECEPTIONIST] Appointment booked by phone.",
            f"Callback: {callback_number}",
        ]
        if caller_email:
            description_lines.append(f"Email: {caller_email}")
        if notes:
            description_lines.append(f"Notes: {notes}")
        record = await self._request(
            "POST",
            "Meeting",
            payload={
                "name": f"[AI] {service} - {caller_name}",
                "status": "Planned",
                "dateStart": _format_espo_datetime(start),
                "dateEnd": _format_espo_datetime(end),
                "description": "\n".join(description_lines),
                "parentId": account["id"],
                "parentType": "Account",
                "parentName": account["name"],
                "assignedUserId": user["id"],
                "assignedUserName": user.get("name") or user.get("userName"),
                "contactsIds": [contact.id],
                "contactsNames": {contact.id: contact.name},
            },
        )
        appointment_id = record["id"]
        return BookingResult(
            event_id=appointment_id,
            start_iso=start.isoformat(),
            end_iso=end.isoformat(),
            html_link=f"{self.config.base_url}/#Meeting/view/{appointment_id}",
        )

    async def reschedule_appointment(
        self,
        *,
        appointment_id: str,
        contact_id: str,
        new_start: datetime,
    ) -> CRMAppointment:
        appointment = await self.appointment_for_contact(
            appointment_id, contact_id
        )
        if appointment.status != "Planned":
            raise CRMRequestError("Only planned appointments can be rescheduled")
        duration = appointment.end - appointment.start
        new_end = new_start + duration
        await self._assert_slot_available(
            new_start,
            new_end,
            exclude_appointment_id=appointment.id,
        )
        record = await self._request(
            "PUT",
            f"Meeting/{appointment.id}",
            payload={
                "dateStart": _format_espo_datetime(new_start),
                "dateEnd": _format_espo_datetime(new_end),
                "description": (
                    appointment.description
                    + "\n[AI RECEPTIONIST] Appointment rescheduled by phone."
                ).strip(),
            },
        )
        return _appointment_from_record(record)

    async def cancel_appointment(
        self,
        *,
        appointment_id: str,
        contact_id: str,
        reason: str | None = None,
    ) -> CRMAppointment:
        appointment = await self.appointment_for_contact(
            appointment_id, contact_id
        )
        if appointment.status != "Planned":
            raise CRMRequestError("Only planned appointments can be cancelled")
        note = "[AI RECEPTIONIST] Appointment cancelled by phone."
        if reason:
            note += f" Reason: {reason}"
        record = await self._request(
            "PUT",
            f"Meeting/{appointment.id}",
            payload={
                "status": "Not Held",
                "description": (appointment.description + "\n" + note).strip(),
            },
        )
        return _appointment_from_record(record)

    async def search_knowledge(
        self,
        question: str,
        *,
        limit: int = 3,
    ) -> list[CRMKnowledgeResult]:
        records = await self.list_records(
            "KnowledgeBaseArticle",
            where=[{
                "type": "equals",
                "attribute": "status",
                "value": "Published",
            }],
            select=["id", "name", "bodyPlain", "body", "description"],
            max_size=100,
            order_by="name",
        )
        query_tokens = self._expanded_tokens(question)
        ranked: list[CRMKnowledgeResult] = []
        for record in records:
            title = record.get("name") or ""
            body = record.get("bodyPlain") or record.get("body") or ""
            body = html.unescape(_HTML_TAG_RE.sub(" ", body)).strip()
            title_tokens = self._expanded_tokens(title)
            body_tokens = self._expanded_tokens(body)
            score = 3 * len(query_tokens & title_tokens) + len(
                query_tokens & body_tokens
            )
            if score:
                ranked.append(CRMKnowledgeResult(
                    id=record["id"],
                    title=title,
                    body=body,
                    score=score,
                ))
        ranked.sort(key=lambda item: (-item.score, item.title))
        return ranked[:limit]

    @staticmethod
    def _expanded_tokens(value: str) -> set[str]:
        tokens = {
            token for token in _normal_text(value).split()
            if len(token) > 2 and token not in _STOP_WORDS
        }
        expanded = set(tokens)
        for topic, synonyms in _TOPIC_SYNONYMS.items():
            group = {topic, *synonyms}
            if tokens & group:
                expanded.update(group)
        return expanded
