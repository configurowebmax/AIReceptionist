# Netelip + LiveKit Build: llamadas entrantes

Esta ruta conecta un número virtual de Netelip con el agente
`receptionist` mediante un trunk SIP entrante de LiveKit Cloud:

```text
Teléfono -> DID Netelip -> SIP -> LiveKit -> receptionist -> Gemini + EspoCRM
```

El repositorio automatiza la parte de LiveKit. La compra y el enrutamiento
del número se completan en el panel de Netelip porque requieren una cuenta,
verificación del titular y, en algunos países, documentación adicional.

## Alcance

La configuración incluida aquí cubre llamadas **entrantes**, suficiente para
probar consultas y gestión de citas. No crea llamadas salientes ni garantiza
transferencias SIP REFER; esas funciones dependen de que Netelip las habilite
para el trunk contratado.

El plan Build gratuito de LiveKit incluye actualmente 1.000 minutos mensuales
de SIP de terceros y 1.000 minutos de sesión de agente. En el plan gratuito son
límites rígidos, no consumo facturable adicional:

https://docs.livekit.io/deploy/admin/quotas-and-limits/

## 1. Datos necesarios

Obtén:

- Un DID de Netelip, por ejemplo `+576011234567`.
- El endpoint SIP del proyecto LiveKit, por ejemplo
  `abc123.sip.livekit.cloud`.
- `LIVEKIT_URL`, `LIVEKIT_API_KEY` y `LIVEKIT_API_SECRET`.
- Las IP o rangos CIDR desde los cuales Netelip enviará los INVITE SIP.

Netelip publica actualmente `192.99.91.144` para su servidor de América
Latina. El aprovisionador lo usa como valor inicial
`192.99.91.144/32`, pero debes confirmarlo con soporte antes de producción,
especialmente si Netelip utiliza proxies adicionales o failover.

Referencias:

- https://www.netelip.com/centro-de-ayuda/inicio-administradores/integraciones-con-crms/integrar-livekit-netelip-sip-trunk/
- https://www.netelip.com/centro-de-ayuda/sip-trunking/preguntas-frecuentes/
- https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/

## 2. Configurar variables locales

Edita `.env` sin compartir ni guardar en Git los valores reales:

```dotenv
LIVEKIT_URL=wss://tu-proyecto.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
RECEPTIONIST_AGENT_NAME=receptionist

NETELIP_DID=+576011234567
LIVEKIT_SIP_ENDPOINT=abc123.sip.livekit.cloud
NETELIP_ALLOWED_ADDRESSES=192.99.91.144/32
NETELIP_SECURITY_MODE=auto
NETELIP_SIP_USERNAME=
NETELIP_SIP_PASSWORD=
```

Para varias IP:

```dotenv
NETELIP_ALLOWED_ADDRESSES=192.99.91.144/32,OTRA_IP/32
```

`LIVEKIT_SIP_ENDPOINT` no siempre coincide con el hostname de
`LIVEKIT_URL`. Cópialo desde la configuración SIP del proyecto.

El modo `auto` usa autenticación SIP si completas usuario y contraseña; si
están vacíos, usa la IP permitida. Esto es útil porque `allowed_addresses`
puede requerir habilitación por parte de LiveKit. Si Netelip te entrega
credenciales SIP para el trunk, es preferible completar ambas y mantener
`NETELIP_SECURITY_MODE=auto`. Los secretos se escriben únicamente bajo
`secrets/`, que Git ignora.

## 3. Generar y revisar el plan

```powershell
.\.venv\Scripts\python.exe -m receptionist.telephony netelip plan
```

También puedes pasar los valores explícitamente:

```powershell
.\.venv\Scripts\python.exe -m receptionist.telephony netelip plan `
  --number +576011234567 `
  --sip-endpoint abc123.sip.livekit.cloud `
  --business example-dental `
  --allowed-address 192.99.91.144/32
```

Se crean tres archivos privados bajo `secrets/netelip/`:

- `livekit-inbound-trunk.json`
- `livekit-dispatch-rule.template.json`
- `netelip-routing.json`

La carpeta `secrets/` está excluida de Git. El plan valida E.164, hostname SIP,
slug del negocio y rangos IP antes de escribir.

## 4. Crear los recursos en LiveKit

Primero ejecuta un dry-run:

```powershell
.\.venv\Scripts\python.exe -m receptionist.telephony netelip apply
```

Después de revisar el plan:

```powershell
.\.venv\Scripts\python.exe -m receptionist.telephony netelip apply --yes
```

El comando usa el SDK de LiveKit instalado en el proyecto. Crea o reutiliza:

1. El trunk entrante `netelip-example-dental-inbound`.
2. Una regla de despacho por llamada.
3. El destino al agente `receptionist`.
4. Los metadatos `{"config":"example-dental"}` para seleccionar la clínica.

La operación es idempotente: si encuentra recursos con el mismo nombre y la
misma configuración, los reutiliza. Si encuentra el mismo nombre con otro DID
o distintas IP permitidas, se detiene para no modificar recursos por error.

## 5. Enrutar el número en Netelip

En `panel.netelip.com`:

1. Activa el servicio SIP Trunk.
2. Asocia el DID comprado al trunk.
3. En el enrutamiento del número, establece como servidor/destino el valor de
   `LIVEKIT_SIP_ENDPOINT`, sin `https://`.
4. Selecciona formato de número `+E.164`.
5. Utiliza TCP, salvo que Netelip indique UDP para tu cuenta.
6. Guarda y llama al DID.

No configures un desvío hacia otro número PSTN: eso genera una segunda llamada
facturable. El destino debe ser el endpoint SIP de LiveKit.

## 6. Ejecutar y probar el agente

```powershell
.\.venv\Scripts\python.exe -m receptionist.agent dev
```

Prueba:

1. Consultar seguros y ubicación.
2. Identificarse como `Ana Torres`, teléfono `+12025550101`.
3. Consultar la cita.
4. Reprogramarla después de confirmar una hora ofrecida.
5. Cancelarla con confirmación explícita.

En LiveKit Cloud revisa **Telephony** y **Sessions**. Debes ver una sala con
prefijo `netelip-example-dental-` y el agente `receptionist`.

## Errores frecuentes

- **403:** las IP permitidas o la autenticación del trunk no coinciden.
- **404:** el DID no coincide en formato E.164 o no está asociado al trunk.
- **La llamada entra pero nadie responde:** la Dispatch Rule no apunta al
  agente `receptionist`, el proceso no está ejecutándose o Gemini no tiene una
  API key válida.
- **Audio en un solo sentido:** confirma G.711 PCMA/PCMU y el transporte SIP
  con Netelip.
- **No aparece Caller ID:** confirma que Netelip envíe ANI en formato `+E.164`.
