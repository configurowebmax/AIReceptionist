# LiveKit native phone number demo

This is the shortest path for an inbound demonstration. LiveKit Cloud provides
the US phone number and routes calls directly to the agent, so no third-party
SIP trunk is required.

Check LiveKit's current pricing and limits before renting a number. Native
LiveKit numbers are currently US-only and inbound-only. They do not currently
support outbound calls or SIP participant transfers.

## 1. Keep credentials local

Copy the public template and fill in the local file:

```powershell
Copy-Item .env.example .env
```

Required values for the default dental demo:

```dotenv
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
RECEPTIONIST_AGENT_NAME=receptionist
GOOGLE_API_KEY=your-google-api-key
ESPOCRM_USERNAME=admin
ESPOCRM_PASSWORD=your-local-espocrm-password
```

Never commit `.env`, LiveKit CLI configuration, Google keys, CRM passwords,
call logs, transcripts, recordings, or caller phone numbers. This repository's
`.gitignore` excludes the standard local locations.

## 2. Rent a US number

In LiveKit Cloud:

1. Open **Telephony -> Phone Numbers**.
2. Select **Rent a number**.
3. Search for a US local area code.
4. Confirm the number rental.

The same operation is available through the LiveKit CLI:

```powershell
lk number search --country-code US --area-code 305 --limit 5
lk number purchase --numbers +1XXXXXXXXXX
```

Purchasing or releasing a number can affect billing. Do not automate either
operation without explicit approval from the account owner.

## 3. Create explicit agent dispatch

Create an individual dispatch rule so every caller receives a private room and
the `receptionist` worker is explicitly dispatched. In
**Telephony -> Dispatch rules**, use the JSON editor:

```json
{
  "rule": {
    "dispatchRuleIndividual": {
      "roomPrefix": "call-"
    }
  },
  "name": "LiveKit phone to receptionist",
  "roomConfig": {
    "agents": [
      {
        "agentName": "receptionist",
        "metadata": "{\"config\":\"example-dental\"}"
      }
    ]
  }
}
```

The `agentName` must match `RECEPTIONIST_AGENT_NAME`. The metadata selects
`config/businesses/example-dental.yaml` without placing any credential in the
dispatch rule.

## 4. Assign the rule to the number

From **Telephony -> Phone Numbers**, open the number's menu, choose
**Configure with existing dispatch rules**, select the rule created above, and
save.

CLI equivalent:

```powershell
lk number list --json
lk sip dispatch list --json
lk number update --id PN_REPLACE_ME --sip-dispatch-rule-id SDR_REPLACE_ME
```

The identifiers above are placeholders. Do not publish identifiers copied from
a private LiveKit project.

## 5. Optional local EspoCRM

Start the local CRM and load the fictional dataset:

```powershell
Copy-Item infra/espocrm/.env.example infra/espocrm/.env
docker compose --env-file infra/espocrm/.env -f infra/espocrm/compose.yaml up -d
.\.venv\Scripts\python.exe infra\espocrm\seed_demo.py
```

The example business reads published knowledge articles and appointment data
from `http://localhost:8080`. If the agent is deployed to LiveKit Cloud or
another remote host, `localhost` points to that remote container, not this
computer. Host EspoCRM at a private HTTPS address before deploying the agent.

## 6. Run and test

```powershell
.\.venv\Scripts\python.exe -m receptionist.agent dev
```

Wait for a `registered worker` log with agent name `receptionist`, then call
the rented number. Test business hours, location, insurance, and appointment
creation. Confirm that logs, transcripts, and caller numbers remain outside
Git before publishing the repository.

Official references:

- <https://docs.livekit.io/telephony/start/phone-numbers/>
- <https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/>
- <https://ai.google.dev/gemini-api/docs/api-key>
