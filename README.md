# Claims Desk

**Live demo:** [https://insurance-sop-agent-zmss.onrender.com/](https://insurance-sop-agent-zmss.onrender.com/)

Paste your own provider API key on the left panel, then try the sample calls. The free Render instance may take a minute to wake up after idle time.

A claims-support agent that follows a fixed procedure and still talks like a person.

Python decides the phase, the identity gate, which claim is open, and whether an email can be sent. The model only does two narrow jobs: read messy language into fields, and phrase the reply from facts the controller already chose. With no server key and no visitor key, the desk answers with a fixed “language model is not configured” notice and does not advance the call. Paste a key in the left panel (or set `LLM_API_KEY`) to run the procedure. If the model fails a safety check, the reply falls back to a phase template.

The reference harness shows the model the whole claim file and checks the reply afterwards. This desk seals the directive first: before verification the prompt has no claim fields, and after verification only the fields for the current path (payment figures only when the caller asked about status or amount). The extractor cannot widen a turn onto those figures.

## Phases

`VERIFY_ID` → `RESOLVE_INTENT` → `PROCESS_CASE` → `POST_PROCESS`

| Phase | Freedom | What the caller gets |
| --- | --- | --- |
| Verify | Strict | No claim facts until 3 of 5 details match: full name, date of birth, phone, email, last 4 of SSN or government ID. The policy number finds the account and does not count. |
| Intent | Flexible | The controller picks a claim from what the caller already said, or asks which file when more than one fits. |
| Case | Flexible, grounded | Answers come from the claim record and the document guideline. Invented case ids and amounts are discarded. |
| Follow-up | Choice | The caller can send a summary to the masked address on file, or skip it. |

Anything the caller says is stored for a later phase. “Denied healthcare claim from January,” said before verification, is used after the gate instead of asking again.

Questions that are not about claims service, such as “What is RL?”, are declined. After three in a row the call is transferred. The same happens when frustration or refusal makes no progress. Anger changes the tone. It does not open the file.

A caller on someone else’s behalf is matched to `fixtures/representatives.json` and waits for the scripted consent in `fixtures/consent_scenarios.json`. Reciting the policyholder’s date of birth or ID does not verify them.

## Run with Docker

From this directory:

```bash
cp .env.example .env
```

Put a key in `.env` if you want the model to phrase replies.

**Claude / Anthropic:**

```bash
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
# or: LLM_API_KEY=sk-ant-...
# optional: LLM_MODEL=claude-haiku-4-5-20251001
```

**OpenAI:**

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# or: LLM_API_KEY=sk-...
```

**Other hosts** (OpenAI-compatible chat completions): set `LLM_PROVIDER` to `openrouter`, `ollama`, `groq`, `together`, `deepseek`, `mistral`, `gemini`, or `fireworks`, or keep `openai` and set `LLM_BASE_URL` yourself. Provider-specific keys such as `OPENROUTER_API_KEY` and `GOOGLE_API_KEY` are accepted.

```bash
docker compose up --build
```

Open http://localhost:8000. If that port is taken:

```bash
APP_PORT=8010 docker compose up --build
```

`GET /healthz` reports `provider`, `transport`, `model`, `host`, and `llm_configured`. A 401, 403, or 404 from the model host returns a setup notice and does not change the call.

## Deploy on Render

1. Push this repo to GitHub (do not commit `.env`).
2. On [Render](https://dashboard.render.com): **New → Web Service** → connect the repo.
3. Use **Docker** runtime, Dockerfile at the repo root.
4. You do not need to put an API key on Render. Each visitor picks a provider and model on the page and pastes their own key. The key stays in that browser tab and is sent only with their messages. It is not saved in the session database.
5. Optional server key (local Docker, or a private demo): `LLM_PROVIDER`, `ANTHROPIC_API_KEY` or `LLM_API_KEY`, and `LLM_MODEL`. A visitor key overrides it for that turn.
6. Deploy. The container listens on `$PORT` (Render sets this; locally it defaults to `8000`).
7. Open the service URL, paste a key, and check `/healthz`.

This project’s public demo: [https://insurance-sop-agent-zmss.onrender.com/](https://insurance-sop-agent-zmss.onrender.com/)

Sessions use SQLite under `DATA_DIR`. On free Render that disk is ephemeral unless you attach a persistent Disk at `/srv/data`.

Before a reply reaches the caller, an output guard blocks claim leaks and false “you're verified” lines, requires a mismatch statement and human offer when those flags are set, checks remaining-factor counts, and rejects invented case ids after verification. A hard violation regenerates once, then falls back to the templated reply.

Sample calls, including the frustrated caller who demands the denial before verifying, are walked through in [docs/walkthroughs.md](docs/walkthroughs.md). The workflow diagram is in [docs/design.md](docs/design.md).

| Button | What to expect |
| --- | --- |
| **Margaret’s denial** | Verifies, opens `CL-2048`, explains the denial from the record without re-asking. |
| **Representative consent** | David for Margaret; consent pending, then approved on the next turn (or times out with the Consent script). |
| **Frustrated caller** | Stays in Verify; no claim facts; empathy only. |
| **Partial ID** | Name alone; still asks for more factors. |
| **Out of scope** | Declines “What is reinforcement learning?” |
| **Wrap up** / **Email yes** / **Email no** | Use after a case is open: offer → send to the masked address, or skip. |

After Margaret’s denial, try “What documents do I need to send and by when?”, then **Wrap up** and **Email yes**. The letter tray shows the three-part summary.

Sessions are stored in `./data` and survive a container restart. Run either Compose or a local server, not both at once, because they share that database. The API never returns raw date of birth, phone, email, or last-4. The stored transcript shows `[DATE]`, `[PHONE]`, `[EMAIL]`, and `[ID4]`.

## Run without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests

Use the project venv so Anaconda’s global `pytest` (and plugins like napari) do not get loaded:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

Or without activating:

```bash
.venv/bin/python -m pytest -q
```

If the prompt shows both `(.venv)` and `(base)`, run `conda deactivate` first, or always call `.venv/bin/python -m pytest -q`. The suite uses a fake model and does not need a key or a network connection.

### Live scenario runner

With the server up and a key configured (server `.env` or visitor env vars):

```bash
.venv/bin/python scripts/live_demo.py
.venv/bin/python scripts/live_demo.py --scenario demo_caller --verbose
```

For a BYOK deploy with no server key:

```bash
export CLAIMS_LLM_PROVIDER=groq
export CLAIMS_LLM_MODEL=openai/gpt-oss-120b
export CLAIMS_LLM_API_KEY=gsk_...
.venv/bin/python scripts/live_demo.py --base-url https://your-service.onrender.com
```

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `LLM_API_KEY` | empty | Token for the model host |
| `LLM_MODEL` | provider default | Override when a model name returns 404 |
| `LLM_BASE_URL` | provider default | OpenAI-compatible base URL |
| `MAX_VERIFY_ATTEMPTS` | `3` | Mismatches before a person is offered. One more mismatch transfers. |
| `MAX_OFF_TOPIC` | `3` | Off-topic turns before transfer |
| `MAX_FRUSTRATION_TURNS` | `3` | Frustrated turns with no progress before transfer |
| `TRACE_ENABLED` | `true` | Include the turn decision list in the API response |
| `APP_PORT` | `8000` | Host port published by Compose |

## Layout

```
app/
  main.py              FastAPI + BYOK endpoints
  sop/
    engine.py          Turn loop (extract → decide → phrase → guard)
    directives.py      Shared directive builders
    phases/            VERIFY_ID, RESOLVE_INTENT, PROCESS_CASE, POST_PROCESS
    parse.py           Deterministic field reading
    firewall.py        Seal claim facts before the model speaks
    guard.py           Output guard
    phrases.py         Templated fallbacks
  eval/                Shared offline + live scenario definitions
  llm/                 Multi-provider client + NullLLM
  static/              Claims-desk UI
fixtures/              Policyholders, claims, guidelines, representatives, consent
scripts/live_demo.py   Live PASS/FAIL runner (needs a running server + key)
tests/                 Offline suite (FakeLLM / NullLLM, no network)
docs/                  design, walkthroughs, behaviour-spec, transcripts
docker/                Entrypoint that keeps the data directory writable
```

## Documentation

- [docs/design.md](docs/design.md) — phases, the workflow diagram, firewall, identity, memory, emotion, consent.
- [docs/walkthroughs.md](docs/walkthroughs.md) — how to run the demo, including the frustrated caller.
- [docs/behaviour-spec.md](docs/behaviour-spec.md) — each rule, the code that enforces it, and the test that pins it.
- [docs/transcripts.md](docs/transcripts.md) — expected calls, phase by phase.
