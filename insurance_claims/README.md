# Harborline Claims Desk

A claims-support agent that follows a fixed procedure and still talks like a person.

Python decides the phase, the identity gate, which claim is open, and whether an email can be sent. The model only does two narrow jobs: read messy language into fields, and phrase the reply from facts the controller already chose. With no API key, those replies fall back to templates, so the procedure still runs.

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

Put a key in `.env` if you want the model to phrase replies:

```bash
LLM_API_KEY=sk-...
# LLM_PROVIDER=anthropic
# LLM_MODEL=gpt-4o-mini
# LLM_BASE_URL=https://api.openai.com/v1
```

OpenAI-compatible hosts work too. Point `LLM_BASE_URL` at the base that precedes `/chat/completions` (OpenRouter, Ollama, vLLM).

```bash
docker compose up --build
```

Open http://localhost:8000. If that port is taken:

```bash
APP_PORT=8010 docker compose up --build
```

`GET /healthz` reports `llm_configured: true` when a key was provided. A 401, 403, or 404 from the model host returns a setup notice and does not change the call.

The sample button **Margaret’s denial** sends:

> I’m the policyholder. My name is Margaret Chen, policy POL-9921. I’m calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.

The desk should verify her, open `CL-2048`, and explain the denial from the record: the file was missing the pathology report and the office note. It should not have asked her to describe the claim again.

Then try “What documents do I need to send and by when?”, “That’s all, thanks.”, and “Yes please send it.” The letter tray shows the three-part summary. “No thanks” closes without sending.

Sessions are stored in `./data` and survive a container restart. Run either Compose or a local server, not both at once, because they share that database. The API never returns raw date of birth, phone, email, or last-4. The stored transcript shows `[DATE]`, `[PHONE]`, `[EMAIL]`, and `[ID4]`.

## Run without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests

```bash
pip install -r requirements.txt
python -m pytest -q
```

The suite uses a fake model. It does not need a key or a network connection.

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
app/            API, SOP controller, model client, claims-desk page
fixtures/       Policyholders, claims, guidelines, representatives, consent
tests/          Gate tests against a fake model
docker/         Entrypoint that keeps the data directory writable
```
