# TradeFlow-OS Pro

Multi-tenant voice AI platform for trade contractors (plumbing, HVAC, roofing, electrical, garage door, locksmith, towing).

Retell AI handles voice (STT → TTS → turn management). Claude (`claude-sonnet-4-6`) runs the agent logic, calls tools mid-conversation, and returns responses for Retell to speak.

---

## Architecture

```
Caller → Retell AI (voice) → FastAPI webhooks → ClaudeAgent → Tools → DB / Twilio / Retell
```

**Stack:** Python 3.11 · FastAPI · Anthropic Claude · Retell AI · PostgreSQL · Twilio · APScheduler · Docker

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd tradeflow-os-pro
cp .env.example .env
# Fill in all values in .env
```

### 2. Run with Docker

```bash
docker-compose up --build
```

API available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

### 3. Run database migrations

```bash
docker-compose exec api alembic upgrade head
```

### 4. Run locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set DATABASE_URL to a local Postgres instance in .env
alembic upgrade head
uvicorn app.main:app --reload
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `RETELL_API_KEY` | Retell AI API key |
| `RETELL_WEBHOOK_SECRET` | Retell webhook signing secret (HMAC-SHA256) |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Outbound SMS number (E.164 format) |
| `DATABASE_URL` | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `SECRET_KEY` | App secret key |
| `DEBUG` | `true` enables SQL echo and verbose logging |
| `CLAUDE_MODEL` | Defaults to `claude-sonnet-4-6` |
| `CLAUDE_MAX_TOKENS` | Defaults to `1024` |

---

## API Endpoints

### Health
```
GET /health
```

### Retell Webhooks (called by Retell AI — require `X-Retell-Signature` header)
```
POST /retell/call-started
POST /retell/call-update
POST /retell/call-ended
POST /retell/missed-call
```

### Contractor Management (require `X-API-Key` header)
```
POST   /contractors                              Create contractor
GET    /contractors/{id}                         Get contractor
PUT    /contractors/{id}                         Update contractor
GET    /contractors/{id}/leads                   List leads (paginated + filtered)
GET    /contractors/{id}/leads/{lead_id}         Get single lead
```

### Leads
```
GET /leads/{lead_id}          Get lead by ID (scoped to authenticated contractor)
```

---

## First Milestone Test (manual end-to-end)

**1. Create a contractor**

```bash
# Replace YOUR_BOOTSTRAP_KEY with any key that already exists in the DB,
# or seed one directly via psql first.
curl -X POST http://localhost:8000/contractors \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_BOOTSTRAP_KEY" \
  -d '{
    "name": "ABC Plumbing Ltd",
    "agent_name": "Alex",
    "phone_number": "+15550001234",
    "api_key": "my-contractor-key-001",
    "trades": ["plumbing"],
    "service_areas": ["T2N", "T2P", "Calgary"],
    "timezone": "America/Edmonton",
    "diagnostic_fee": 99,
    "sms_enabled": false
  }'
```

**2. Simulate call-started**

```bash
BODY='{"call_id":"test-001","from_number":"+15559998888","to_number":"+15550001234","metadata":{}}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$RETELL_WEBHOOK_SECRET" | awk '{print $2}')

curl -X POST http://localhost:8000/retell/call-started \
  -H "Content-Type: application/json" \
  -H "X-Retell-Signature: $SIG" \
  -d "$BODY"
```

**3. Simulate first user turn**

```bash
BODY='{"call_id":"test-001","transcript":[{"role":"user","content":"I have a burst pipe, water is everywhere"}],"turntaking":"user"}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$RETELL_WEBHOOK_SECRET" | awk '{print $2}')

curl -X POST http://localhost:8000/retell/call-update \
  -H "Content-Type: application/json" \
  -H "X-Retell-Signature: $SIG" \
  -d "$BODY"
```

Claude will respond with emergency triage language and call `check_availability` with `urgency=emergency`.

**4. Continue the conversation** — provide name, address, confirm a slot.

**5. Check the lead record**

```bash
curl http://localhost:8000/contractors/{contractor_id}/leads \
  -H "X-API-Key: my-contractor-key-001"
```

Verify `appointment_status=booked`, scores populated, all fields present.

---

## Retell Configuration

TradeFlow-OS Pro uses Retell's **Custom LLM** integration mode. There are two independent surfaces:

### 1. WebSocket — Custom LLM (real-time turns)
Retell opens a WebSocket to your server for every call and exchanges turns in real time.

```
wss://your-domain.com/llm-websocket/{call_id}
```

**Message protocol:**
| Direction | Event | Purpose |
|---|---|---|
| Retell → Server | `call_details` | First message — call metadata, triggers agent init + greeting |
| Retell → Server | `response_required` | User finished speaking — send Claude's response |
| Retell → Server | `update_only` | Mid-speech transcript update — no response needed |
| Retell → Server | `call_ended` | Call disconnected — finalise session |
| Server → Retell | `{response_id, content, content_complete, end_call}` | Agent text to speak |

### 2. HTTP Webhook — lifecycle events
```
POST /retell/webhook          call_started, call_ended, call_analyzed
POST /retell/missed-call      missed inbound call
```
All requests verified with HMAC-SHA256 (`x-retell-signature` header, key = `RETELL_WEBHOOK_SECRET`).

### Setup steps

**Step 1 — Create contractor and provision Retell agent (one-time per contractor):**
```bash
# 1. Create the contractor record
curl -X POST http://localhost:8000/contractors \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "ABC Plumbing", "agent_name": "Alex", "phone_number": "+15550001234", ...}'

# 2. Provision the Retell agent (creates Custom LLM agent, stores agent_id)
curl -X POST "http://localhost:8000/contractors/{id}/provision-retell-agent?public_base_url=https://your-domain.com&voice_id=11labs-Adrian" \
  -H "X-API-Key: YOUR_KEY"
```

**Step 2 — In Retell dashboard:**
1. Assign your phone number to the provisioned agent.
2. Set webhook URL: `https://your-domain.com/retell/webhook`
3. Set missed-call webhook: `https://your-domain.com/retell/missed-call`
4. Copy the webhook secret into `RETELL_WEBHOOK_SECRET`.

**Step 3 — Verify:**
The `to_number` on every inbound call must match a `Contractor.phone_number` in your DB. The platform looks up the contractor from the phone number to load the correct system prompt and agent persona.

---

## Running Tests

```bash
pip install -r requirements.txt
pytest
```

Tests use SQLite in-memory (no Postgres required). External APIs (Anthropic, Twilio, Retell) are mocked.

---

## Project Structure

```
app/
├── main.py                  FastAPI app, lifespan, middleware
├── config.py                Pydantic settings (all from env)
├── database.py              Async SQLAlchemy engine + session
├── models/                  SQLAlchemy ORM models
├── schemas/                 Pydantic v2 request/response schemas
├── routers/
│   ├── retell.py            Retell webhook endpoints
│   ├── contractors.py       Contractor CRUD + lead listing
│   └── leads.py             Lead fetch
├── services/
│   ├── claude_agent.py      Agentic conversation loop (core)
│   ├── retell_client.py     Retell REST API client
│   ├── sms.py               Twilio SMS service
│   ├── calendar.py          Calendar availability (MVP + stubs)
│   ├── lead_scoring.py      Score inference + priority derivation
│   └── scheduler.py         APScheduler jobs (reminders, recovery)
├── tools/
│   ├── definitions.py       Claude tool JSON schemas
│   ├── handlers.py          Tool call router
│   └── *.py                 Individual tool implementations
└── prompts/
    ├── master_prompt.py     19-section TradeFlow AI system prompt
    └── builder.py           Prompt builder (fills contractor variables)
```

---

## Deferred (v2)

- Google Calendar / Calendly live integration (stubs in `calendar.py`)
- Multi-language support
- Admin dashboard UI
- Stripe billing
- Real-time websocket call monitoring
- Redis-backed agent session store (currently in-memory)
