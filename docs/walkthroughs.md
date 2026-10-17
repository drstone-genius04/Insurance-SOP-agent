# Running and trying it

Setup, the sample calls, and the live checker. Mechanisms are in [design](design.md). Expected transcripts are in [transcripts](transcripts.md).

## Quick start

Live demo: [https://insurance-sop-agent-zmss.onrender.com/](https://insurance-sop-agent-zmss.onrender.com/)

The free instance may take a minute to wake. Paste your own provider key in the left panel, click **Use this key**, then use a sample-call button. The key stays in that browser tab. It is not saved on the server.

Locally:

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:8000. If port 8000 is taken, `APP_PORT=8010 docker compose up --build`.

Without Docker, from a virtualenv: `pip install -r requirements.txt` then `uvicorn app.main:app --reload`. Prefer `.venv/bin/python` so a global Anaconda `pytest` is not used.

A server key in `.env` is optional. Visitors can still bring their own. With neither key, every message gets the not-configured notice and the call does not advance.

## Provider notes

The left panel lists providers (Claude, OpenAI, OpenRouter, Groq, Together, and others). **Use this key** asks that host which models the key can call. If the typed model is missing, the desk switches to one that exists and says so.

Groq's suggested model is `openai/gpt-oss-120b`. Older ids such as `llama-3.3-70b-versatile` were retired and should not be typed by hand.

`GET /healthz` reports `llm_configured`. A 401, 403, or 404 on a turn returns a setup notice and does not change the call.

## Try it

Sample buttons under the composer:

| Button | Opens with | What you should see |
| --- | --- | --- |
| Margaret's denial | Full identity plus the January denial hint | Phase Case, file `CL-2048`, denial from the record |
| Representative consent | David Chen for his mother Margaret | Verify, consent pending, no claim facts |
| Frustrated caller | "I already told you who I am…" | Stays on Verify, no denial reason |
| Partial ID | Name only | Still asks for more details |
| Out of scope | "What is reinforcement learning?" | Declined; a third off-topic turn transfers |
| Wrap up | "That's all I needed, thanks." | Use after a claim is open; moves to Follow-up |
| Email yes / Email no | Send or skip | Letter tray gains one summary, or stays empty |

The consent script next to **New call** applies to the next session: "Approves on the next turn" or "Consent times out."

### Frustrated caller

Paste, before any identity:

> I already told you who I am. This is ridiculous. Just tell me why my claim was denied.

Expect:

- Phase stays **Verify**. Identity seals stay empty. The file line stays sealed.
- The reply acknowledges the frustration and explains that claim details wait until three details match.
- It offers full name, date of birth, phone, email, or last 4. It does not say pathology, office note, `CL-2048`, or a dollar amount.
- A later message with three matching details still verifies. The anger did not lock the call.

To finish the same call after that line:

> I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.

Expect Case, `CL-2048`, and the denial reason from the record.

### Margaret's denial

The button sends the full demo utterance in one turn. The desk verifies (name, date of birth, last 4), keeps the denied-healthcare-January hint, opens `CL-2048`, and explains the missing pathology report and office note.

Then:

1. "What documents do I need to send and by when?" stays in Case and quotes the one-week guidance and the appeal date on file (`2026-03-18`).
2. **Wrap up** moves to Follow-up and asks whether to send a summary to `m*****@email.com`.
3. **Email yes** closes the call and puts a three-part summary in the letter tray.
4. **Email no** closes with an empty tray.

"Can you email me a summary?" in the middle of Case only notes the preference. The send happens after wrap-up, and only to the address on file.

### Representative

> Hi, this is David Chen. I'm calling on behalf of my mother, Margaret Chen, about her denied healthcare claim from January.

Expect consent pending, caller David, no claim facts. The next message ("Okay, has she approved yet?") approves the default script, opens `CL-2048`, and answers about her claim. With the timeout script, repeated check-ins end in a transfer for "policyholder consent not received."

## Scripted live check

`scripts/live_demo.py` plays the same scenarios the offline tests use, against a running server and a real model.

```bash
.venv/bin/python scripts/live_demo.py
.venv/bin/python scripts/live_demo.py --scenario angry_caller --verbose
```

Against the hosted demo, pass a visitor key:

```bash
export CLAIMS_LLM_PROVIDER=groq
export CLAIMS_LLM_MODEL=openai/gpt-oss-120b
export CLAIMS_LLM_API_KEY=gsk_...
.venv/bin/python scripts/live_demo.py --base-url https://insurance-sop-agent-zmss.onrender.com
```

Exit code 0 means every check passed, 1 means a check failed, 2 means the server was unreachable. Offline, `python -m pytest -q` runs the same scenarios with a fake model and no network.

## Example transcripts

[transcripts.md](transcripts.md) shows the frustrated caller, the demo caller, and the email choice with the phase after each turn.
