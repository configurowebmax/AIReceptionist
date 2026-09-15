# EspoCRM local

La instancia local se abre en <http://localhost:8080>. Las credenciales se
encuentran en el archivo local `.env`, que no debe versionarse.

## Cargar datos demo

Desde la raiz del repositorio:

```powershell
.\.venv\Scripts\python.exe infra\espocrm\seed_demo.py
```

El seed es idempotente: vuelve a encontrar los registros por nombre o correo y
los actualiza en vez de duplicarlos. Crea:

- una cuenta de clinica dental ficticia;
- seis contactos/pacientes con correos `example.test`;
- cuatro citas futuras, una cancelada y una historica completada;
- siete articulos publicados sobre horarios, seguros, ubicacion,
  agendamiento, reprogramacion, cancelacion y urgencias.

Todos los registros incluyen el marcador `[DEMO IA RECEPCIONISTA]` o un
nombre que comienza por `[DEMO]` para que puedan identificarse facilmente.
No se deben sustituir por datos personales reales en un entorno de pruebas.

## Conexion con Tel Assistant

El negocio `config/businesses/example-dental.yaml` ya tiene `crm.enabled:
true`. Para la instalacion local usa `ESPOCRM_USERNAME` y
`ESPOCRM_PASSWORD` desde el `.env` raiz. En produccion se recomienda crear
un API User con permisos limitados a Account, Contact, Meeting y
KnowledgeBaseArticle, y configurar `ESPOCRM_API_KEY`.
