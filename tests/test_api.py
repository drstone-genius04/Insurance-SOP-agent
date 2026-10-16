"""HTTP surface: health, redaction, and the page."""

from fastapi.testclient import TestClient

from tests.conftest import DEMO, say


def test_health_and_page(client: TestClient) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["llm_configured"] is False
    page = client.get("/")
    assert page.status_code == 200
    assert "Claims desk" in page.text
    missing = client.post("/api/session/nope/message", json={"message": "hello"})
    assert missing.status_code == 404
    invalid = client.post("/api/session", json={"consent_scenario": "nope"})
    assert invalid.status_code == 422


def test_transcript_is_redacted(client: TestClient) -> None:
    opened = client.post("/api/session", json={}).json()
    say(client, opened["session_id"], DEMO)
    snapshot = client.get(f"/api/session/{opened['session_id']}")
    assert snapshot.status_code == 200
    transcript = snapshot.json()["transcript"]
    user = next(turn["text"] for turn in transcript if turn["role"] == "user")
    assert "[DATE]" in user
    assert "[ID4]" in user
    assert "1985-03-15" not in user
    assert "4472" not in user
    assert "slots" not in snapshot.json()["state"]
