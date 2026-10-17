# Behaviour specification

Each rule the desk enforces, where it lives, and which test pins it. "Design choice" marks a behaviour that could have gone another way; the reason is in [design](design.md#decisions).

| # | Rule | Code | Test |
| --- | --- | --- | --- |
| 1 | No claim facts until 3 of 5 identity factors match. Policy number locates the account and does not count. | `app/sop/phases/verify_id.py`, `phrases.NEEDED` | `test_flow.py` demo call; `test_scenarios.py` `angry_caller` |
| 2 | A factor counts only when it matches the fixture. A mismatch clears that slot and increments attempts. | `verify_id.handle` | `test_flow.py` mismatch |
| 3 | After `MAX_VERIFY_ATTEMPTS` mismatches, offer a person. One more mismatch transfers. | `verify_id.handle`, `config.Settings` | `test_flow.py` exhaustion |
| 4 | Hints said during verification are stored and used after the gate. Design choice: "January" alone can match two claims; denied + healthcare selects one. | `engine._remember`, `phases/resolve_intent.py` | `test_flow.py` demo and January-only; scenario `disambiguation` |
| 5 | `PROCESS_CASE` answers from the claim and the guideline. The path picks the fields. | `phrases.path_facts`, `phases/process_case.py` | `test_guard.py` path facts; scenario `demo_caller` |
| 6 | Payment fields are visible only on `status_inquiry`, and only if the caller asked about status or amount. Design choice: the extractor cannot widen the path. | `engine._path_allowed`, `firewall.seal` | `test_firewall.py` |
| 7 | Before the model speaks, unverified directives lose claim fields. | `app/sop/firewall.py` | `test_firewall.py` |
| 8 | The output guard blocks leaks, false verification, missing mismatch or human-offer lines, wrong factor counts, invented ids, false "sent", and a stale consent ask. One regeneration, then the template. | `app/sop/guard.py` | `test_guard.py`, `test_flow.py` false-sent |
| 9 | Off-topic questions are declined. Three in a row transfer. | `engine._apply_scope` | scenario `off_topic` |
| 10 | Frustration is acknowledged and does not open the file. A streak without progress transfers. | `phrases.empathy_line`, `engine._apply_emotion` | scenario `angry_caller` |
| 11 | "I already told you who I am. This is ridiculous. Just tell me why my claim was denied." stays in `VERIFY_ID` with zero claim disclosure. | `verify_id.handle` | scenario `angry_caller` turn 1 |
| 12 | Representative match uses the caller's name and the policyholder's name. Their date of birth or last-4 does not verify. | `phases/verify_id.py` `handle_representative` | `test_flow.py` representative PII |
| 13 | Consent advances from `consent_scenarios.json`. Timeout transfers. | `Records.consent_status` | scenario `representative_default`, `representative_timeout` |
| 14 | Email is offered only in `POST_PROCESS`, only to the masked address on file. Yes writes the letter tray and closes. No closes with an empty tray. Two unclear answers close without sending. | `phases/post_process.py` | `test_flow.py` email choice; scenarios `email_skip`, `email_requested_early`, `send_summary_natural` |
| 15 | "I'm done… can you email the summary", "send summary", and "yup" count as wrap-up or yes. | `parse._is_wrap_up`, `parse._read_controls` | `test_flow.py` natural email |
| 16 | A claim that the summary was sent, while `sent` is false, is replaced. | `guard._SENT_CLAIM` | `test_flow.py` false sent |
| 17 | No key: fixed notice, state unchanged. | `engine._turn` | `test_api.py` unconfigured; scenario `unconfigured_notice` |
| 18 | Visitor key is used for that request only and stripped from the response. | `main._caller_client`, `_scrub_secret` | `test_api.py` |
| 19 | A retired model name is replaced when the provider lists a live one. | `main.check_llm` | `test_api.py` model switch |
| 20 | Stored transcript redacts date, phone, email, and last-4. | `app/sop/redact.py` | `test_api.py` transcript redaction |

Scenario names are the keys in `app/eval/scenarios.py`. Offline they run in `tests/test_scenarios.py`. Live they run in `scripts/live_demo.py`.
