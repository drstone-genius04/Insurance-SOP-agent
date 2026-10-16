# References

## 2026-10-16 — Identity name not captured from “My name is …”

- **Issue:** The demo utterance stayed in `VERIFY_ID` with 2 factors. Date of birth and last-4 matched. The full name did not.
- **Root cause:** The name prefix (`my name is`, `I'm`, `this is`) was matched case-sensitively, so `My name is Margaret Chen` never filled the name slot. Turning the whole pattern case-insensitive would also treat `I'm the policyholder` as a name, because `re.I` makes `[A-Z]` match lowercase letters.
- **Solution:** Case-insensitive prefix only, via `(?i:...)`, with the captured name still requiring capitals.
- **Files:** `app/sop/parse.py`
- **Prevention:** Keep a fixture utterance that starts with `My name is` in the gate tests. Do not put `re.I` on the name-capture class.

## 2026-10-16 — “What documents…” treated as off-topic

- **Issue:** After verification, “What documents do I need to send and by when?” was declined as out of scope.
- **Root cause:** The scope word list used `\bdocument\b`, which does not match the plural `documents`. The message then looked like a general question.
- **Solution:** Accept `documents?` and `claims?` in the in-scope vocabulary, and the same plural in the document-intent rule.
- **Files:** `app/sop/parse.py`
- **Prevention:** Phrase gate tests the way a caller would, including plurals.

## 2026-10-16 — Docker session create returned 500

- **Issue:** `POST /api/session` inside the container raised `sqlite3.DatabaseError: database disk image is malformed`.
- **Root cause:** The local server and the container both wrote `./data/sessions.db` at the same time. SQLite does not survive two writers on one file across a Docker volume.
- **Solution:** Stop the local server, delete the damaged database, and let the container create a new one. Run one process against `./data`.
- **Files:** none. Operational fix.
- **Prevention:** Don’t point `uvicorn` and Compose at the same `DATA_DIR` together.
