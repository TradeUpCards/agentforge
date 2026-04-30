"""End-to-end test of the /chat endpoint using FastAPI's TestClient.

Exercises the full path: HMAC verification → tool fetch → LLM call (fixture)
→ verifier → AgentResponse. No uvicorn needed; runs in-process.

Phase 2 sanity check: this should pass before we ship anything.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from agent.config import get_settings
from agent.main import app
from agent.schemas import Role


def _sign(user_id: int, patient_id: int, contents: list[str], secret: str) -> str:
    payload = f"{user_id}|{patient_id}|" + "|".join(contents)
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def test_health() -> None:
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_uc1_starter_returns_verified_response() -> None:
    settings = get_settings()
    client = TestClient(app)

    user_message = "Generate a pre-visit brief for this patient."
    body = {
        "user_id": 1,
        "patient_id": 1,
        "hmac": _sign(1, 1, [user_message], settings.openemr_hmac_secret),
        "messages": [{"role": "user", "content": user_message}],
    }
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["status"] == "ok", payload
    assert payload["message"]["role"] == Role.ASSISTANT.value
    # The fixture's prose mentions metformin + A1c
    assert "metformin" in payload["message"]["content"].lower()
    # All 7 fixture claims should pass verification (record ids match the
    # tool fixtures).
    assert len(payload["claims"]) == 7
    # Tools were called
    assert len(payload["tools_called"]) == 5  # baseline tools
    assert all(t["success"] for t in payload["tools_called"])


def test_chat_with_bad_hmac_returns_refusal() -> None:
    client = TestClient(app)
    body = {
        "user_id": 1,
        "patient_id": 1,
        "hmac": "deadbeef" * 8,  # invalid
        "messages": [{"role": "user", "content": "hi"}],
    }
    r = client.post("/chat", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "refused"
    assert "integrity" in payload["reason"].lower()
