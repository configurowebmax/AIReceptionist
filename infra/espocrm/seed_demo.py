"""Load an idempotent dental-office demo dataset into local EspoCRM.

The script reads the existing infra/espocrm/.env file, authenticates through
EspoCRM's REST API, and creates or updates records identified by stable demo
names/email addresses. It never prints credentials.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DEMO_TAG = "[DEMO IA RECEPCIONISTA]"
BOGOTA = ZoneInfo("America/Bogota")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


class EspoClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        raw_token = f"{username}:{password}".encode("utf-8")
        self.auth = base64.b64encode(raw_token).decode("ascii")

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        body = None
        headers = {
            "Espo-Authorization": self.auth,
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}/api/v1/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"EspoCRM API {method} {path} failed: HTTP {exc.code}: {detail}"
            ) from exc

    def list(self, entity: str) -> list[dict]:
        query = urlencode({"maxSize": 200, "orderBy": "createdAt", "order": "desc"})
        return self.request("GET", f"{entity}?{query}").get("list", [])

    def upsert(
        self,
        entity: str,
        key_field: str,
        key_value: str,
        payload: dict,
    ) -> tuple[str, str]:
        existing = next(
            (record for record in self.list(entity) if record.get(key_field) == key_value),
            None,
        )
        if existing:
            record = self.request("PUT", f"{entity}/{existing['id']}", payload)
            return record["id"], "actualizado"
        record = self.request("POST", entity, payload)
        return record["id"], "creado"


def business_day(start: datetime, offset: int) -> datetime:
    value = start
    remaining = offset
    while remaining:
        value += timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value


def espo_datetime(day: datetime, hour: int, minute: int = 0) -> datetime:
    local = datetime.combine(day.date(), time(hour, minute), tzinfo=BOGOTA)
    return local.astimezone(timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    env = read_env()
    client = EspoClient(
        env.get("ESPOCRM_SITE_URL", "http://localhost:8080"),
        env["ESPOCRM_ADMIN_USERNAME"],
        env["ESPOCRM_ADMIN_PASSWORD"],
    )
    current_user = client.request("GET", "App/user")["user"]

    account_name = "Clinica Dental Sonrisa Demo"
    account_payload = {
        "name": account_name,
        "type": "Customer",
        "phoneNumber": "+12025550100",
        "emailAddress": "recepcion@sonrisa-demo.example.test",
        "website": "https://sonrisa-demo.example.test",
        "billingAddressStreet": "Avenida Demo 123 #45-67, Consultorio 502",
        "billingAddressCity": "Bogota",
        "billingAddressState": "Bogota D.C.",
        "billingAddressPostalCode": "110221",
        "billingAddressCountry": "Colombia",
        "description": (
            f"{DEMO_TAG}\n"
            "Clinica ficticia para probar agendamiento, reprogramacion, "
            "cancelacion y preguntas frecuentes. No representa un negocio real."
        ),
    }
    account_id, action = client.upsert(
        "Account", "name", account_name, account_payload
    )
    print(f"Account: {account_name} ({action})")

    contacts = [
        {
            "firstName": "Ana",
            "lastName": "Torres",
            "emailAddress": "ana.torres@example.test",
            "phoneNumber": "+12025550101",
            "description": (
                f"{DEMO_TAG}\nPaciente DEMO-001. Seguro demo: SURA. "
                "Prefiere citas en la manana."
            ),
        },
        {
            "firstName": "Carlos",
            "lastName": "Mendoza",
            "emailAddress": "carlos.mendoza@example.test",
            "phoneNumber": "+12025550102",
            "description": (
                f"{DEMO_TAG}\nPaciente DEMO-002. Pago particular. "
                "Prefiere citas despues de las 15:00."
            ),
        },
        {
            "firstName": "Laura",
            "lastName": "Gomez",
            "emailAddress": "laura.gomez@example.test",
            "phoneNumber": "+12025550103",
            "description": (
                f"{DEMO_TAG}\nPaciente DEMO-003. Seguro demo: Colsanitas. "
                "Primera visita."
            ),
        },
        {
            "firstName": "Miguel",
            "lastName": "Rodriguez",
            "emailAddress": "miguel.rodriguez@example.test",
            "phoneNumber": "+12025550104",
            "description": (
                f"{DEMO_TAG}\nPaciente DEMO-004. Cita cancelada con "
                "anticipacion; escenario para probar nueva reserva."
            ),
        },
        {
            "firstName": "Sofia",
            "lastName": "Ramirez",
            "emailAddress": "sofia.ramirez@example.test",
            "phoneNumber": "+12025550105",
            "description": (
                f"{DEMO_TAG}\nPaciente DEMO-005. Seguro demo: Allianz. "
                "Tiene una cita historica completada."
            ),
        },
        {
            "firstName": "Diego",
            "lastName": "Vargas",
            "emailAddress": "diego.vargas@example.test",
            "phoneNumber": "+12025550106",
            "description": (
                f"{DEMO_TAG}\nPaciente DEMO-006. Pago particular. "
                "Escenario para consulta de urgencia sin diagnostico."
            ),
        },
    ]

    contact_ids: dict[str, str] = {}
    for contact in contacts:
        full_name = f"{contact['firstName']} {contact['lastName']}"
        payload = {
            **contact,
            "accountId": account_id,
            "accountName": account_name,
        }
        contact_id, action = client.upsert(
            "Contact", "emailAddress", contact["emailAddress"], payload
        )
        contact_ids[full_name] = contact_id
        print(f"Contact: {full_name} ({action})")

    now = datetime.now(BOGOTA)
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    planned_specs = [
        ("Ana Torres", "Valoracion general", 1, 9, 0, 60),
        ("Carlos Mendoza", "Limpieza dental", 2, 16, 0, 45),
        ("Laura Gomez", "Primera consulta", 3, 10, 30, 60),
        ("Diego Vargas", "Valoracion prioritaria", 4, 11, 0, 45),
    ]

    meetings: list[dict] = []
    for patient, service, day_offset, hour, minute, duration in planned_specs:
        starts = espo_datetime(business_day(start_day, day_offset), hour, minute)
        meetings.append(
            {
                "name": f"[DEMO] Cita - {patient} - {service}",
                "status": "Planned",
                "dateStart": format_datetime(starts),
                "dateEnd": format_datetime(starts + timedelta(minutes=duration)),
                "description": (
                    f"{DEMO_TAG}\nServicio: {service}. Estado: confirmada. "
                    "Puede usarse para probar consulta, reprogramacion o cancelacion."
                ),
                "patient": patient,
            }
        )

    cancelled_start = espo_datetime(business_day(start_day, 2), 14)
    meetings.append(
        {
            "name": "[DEMO] Cita - Miguel Rodriguez - Control",
            "status": "Not Held",
            "dateStart": format_datetime(cancelled_start),
            "dateEnd": format_datetime(cancelled_start + timedelta(minutes=45)),
            "description": (
                f"{DEMO_TAG}\nCita cancelada por el paciente con mas de 24 horas "
                "de anticipacion. Sin cargo. Puede reservar una nueva fecha."
            ),
            "patient": "Miguel Rodriguez",
        }
    )

    held_start = espo_datetime(start_day - timedelta(days=7), 9)
    meetings.append(
        {
            "name": "[DEMO] Cita historica - Sofia Ramirez - Limpieza",
            "status": "Held",
            "dateStart": format_datetime(held_start),
            "dateEnd": format_datetime(held_start + timedelta(minutes=45)),
            "description": f"{DEMO_TAG}\nCita completada correctamente.",
            "patient": "Sofia Ramirez",
        }
    )

    for meeting in meetings:
        patient = meeting.pop("patient")
        contact_id = contact_ids[patient]
        payload = {
            **meeting,
            "parentId": account_id,
            "parentType": "Account",
            "parentName": account_name,
            "assignedUserId": current_user["id"],
            "assignedUserName": current_user["name"],
            "contactsIds": [contact_id],
            "contactsNames": {contact_id: patient},
        }
        _, action = client.upsert("Meeting", "name", meeting["name"], payload)
        print(f"Meeting: {meeting['name']} ({action})")

    articles = [
        (
            "[DEMO] Horarios de atencion",
            "La clinica atiende de lunes a viernes de 8:00 a 18:00 y los "
            "sabados de 8:00 a 13:00. Domingos y festivos no hay atencion. "
            "La ultima cita inicia una hora antes del cierre.",
        ),
        (
            "[DEMO] Seguros y formas de pago",
            "Para esta demostracion se aceptan SURA, Colsanitas, Allianz y "
            "Compensar, sujetos a validacion de cobertura. Tambien se acepta "
            "pago particular con tarjeta, transferencia o efectivo. El paciente "
            "debe confirmar aseguradora y numero de documento al reservar.",
        ),
        (
            "[DEMO] Ubicacion, transporte y parqueadero",
            "Direccion ficticia: Avenida Demo 123 #45-67, Centro Medico "
            "Horizonte, consultorio 502, Bogota. Referencia: frente al Parque "
            "Central Demo. Hay parqueadero en el sotano y acceso para movilidad "
            "reducida. Esta ubicacion existe solo para pruebas.",
        ),
        (
            "[DEMO] Politica de agendamiento",
            "La primera consulta dura 60 minutos; controles y limpiezas duran "
            "45 minutos. Se solicita nombre completo, telefono, correo, motivo "
            "de consulta y seguro. El paciente debe llegar 10 minutos antes. "
            "Nunca se debe prometer disponibilidad sin consultar el calendario.",
        ),
        (
            "[DEMO] Reprogramacion de citas",
            "Las citas pueden reprogramarse sin cargo hasta 24 horas antes. "
            "Se debe identificar al paciente, confirmar la cita actual, ofrecer "
            "horarios disponibles y guardar la nueva fecha solo despues de que "
            "el paciente la confirme expresamente.",
        ),
        (
            "[DEMO] Cancelacion de citas",
            "Para cancelar se confirma nombre, telefono y fecha de la cita. "
            "Cancelaciones con al menos 24 horas no tienen cargo. Para esta demo, "
            "las cancelaciones tardias pueden registrar un cargo ficticio de "
            "COP 50.000, pero el asistente debe escalar disputas a recepcion.",
        ),
        (
            "[DEMO] Urgencias y limites del asistente",
            "El asistente no diagnostica ni prescribe. Si hay dificultad para "
            "respirar, sangrado que no se detiene, trauma grave o dolor intenso "
            "con fiebre, debe indicar que se contacte el servicio local de "
            "emergencias y ofrecer transferir a recepcion cuando corresponda.",
        ),
    ]
    publish_date = now.date().isoformat()
    for name, body in articles:
        payload = {
            "name": name,
            "type": "Article",
            "status": "Published",
            "publishDate": publish_date,
            "description": f"{DEMO_TAG} Documento ficticio para pruebas.",
            "body": f"<p>{body}</p>",
            "bodyPlain": body,
            "assignedUserId": current_user["id"],
            "assignedUserName": current_user["name"],
        }
        _, action = client.upsert(
            "KnowledgeBaseArticle", "name", name, payload
        )
        print(f"KnowledgeBaseArticle: {name} ({action})")

    print(
        "Carga demo completada: 1 cuenta, "
        f"{len(contacts)} contactos, {len(meetings)} citas y "
        f"{len(articles)} articulos."
    )


if __name__ == "__main__":
    main()
