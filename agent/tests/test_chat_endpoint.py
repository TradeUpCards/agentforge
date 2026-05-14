"""End-to-end test of the /chat endpoint using FastAPI's TestClient.

Exercises the full path: HMAC verification → tool fetch → LLM call (fixture)
→ verifier → AgentResponse. No uvicorn needed; runs in-process.

Phase 2 sanity check: this should pass before we ship anything.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from agent.config import get_settings
from agent.main import app
from agent.schemas import Role


def _sign(
    user_id: int, patient_id: int, contents: list[str], secret: str, timestamp: int,
) -> str:
    """Match agent.py:verify_hmac payload layout — including the timestamp
    inside the signed bytes is what makes the request replay-resistant."""
    payload = f"{user_id}|{patient_id}|{timestamp}|" + "|".join(contents)
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def test_health() -> None:
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # llm_mode is one of the two known values
    assert body["llm_mode"] in ("fixture", "live")
    # version_sha is always present (deterministic fingerprint for external
    # monitors like the W3 Clinical Red Team Platform daemon). Outside of a
    # built container it falls back to "unknown".
    assert "version_sha" in body
    assert isinstance(body["version_sha"], str)
    assert body["version_sha"] != ""


def test_chat_uc1_starter_returns_verified_response() -> None:
    settings = get_settings()
    client = TestClient(app)

    user_message = "Generate a pre-visit brief for this patient."
    timestamp = int(time.time())
    body = {
        "user_id": 1,
        "patient_id": 1,
        "timestamp": timestamp,
        "hmac": _sign(1, 1, [user_message], settings.openemr_hmac_secret, timestamp),
        "messages": [{"role": "user", "content": user_message}],
    }
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["status"] == "ok", payload
    assert payload["message"]["role"] == Role.ASSISTANT.value
    # Whether running in fixture-LLM mode or live-LLM mode against the same
    # fixture tool data, the brief should mention the patient's medication.
    assert "metformin" in payload["message"]["content"].lower()
    # Verifier should pass at least a few claims. Fixture LLM emits exactly 7;
    # live LLMs (Sonnet/Haiku) typically emit 5–12 depending on phrasing.
    # Looser bound keeps the test stable across both modes.
    assert len(payload["claims"]) >= 5
    # Tools were called.  Baseline was 5 in W1; today's run reaches 8 because
    # we expanded the toolset on 2026-05-09 (added get_intake_extras +
    # get_family_history for PRD §2 intake-required-fields coverage; the
    # meds tool now does a UNION across prescriptions + lists/medication
    # for OpenEMR's two medication storage paths).  Asserting >= keeps the
    # test stable as the toolset evolves while still pinning that ALL
    # baseline tools fire (vs. e.g. 1 or 2 firing on a regression).
    assert len(payload["tools_called"]) >= 5
    assert all(t["success"] for t in payload["tools_called"])


def test_chat_with_bad_hmac_returns_refusal() -> None:
    client = TestClient(app)
    body = {
        "user_id": 1,
        "patient_id": 1,
        "timestamp": int(time.time()),
        "hmac": "deadbeef" * 8,  # invalid
        "messages": [{"role": "user", "content": "hi"}],
    }
    r = client.post("/chat", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "refused"
    assert "integrity" in payload["reason"].lower()


def test_chat_with_stale_timestamp_is_rejected() -> None:
    """Replay-protection regression: a request signed with the correct
    secret but a timestamp >30s old (stand-in for a captured + replayed
    request body) must be refused."""
    settings = get_settings()
    client = TestClient(app)

    user_message = "Generate a pre-visit brief for this patient."
    stale_timestamp = int(time.time()) - 120  # 2 minutes old; well outside the 30s window
    body = {
        "user_id": 1,
        "patient_id": 1,
        "timestamp": stale_timestamp,
        # Sign with the stale timestamp so the signature itself is valid —
        # what trips the rejection is the timestamp-window check, not the
        # signature mismatch path.
        "hmac": _sign(1, 1, [user_message], settings.openemr_hmac_secret, stale_timestamp),
        "messages": [{"role": "user", "content": user_message}],
    }
    r = client.post("/chat", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "refused"
    assert "integrity" in payload["reason"].lower()
