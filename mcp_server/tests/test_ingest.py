import json

import pytest

from rugpull_mcp import ingest, core
from rugpull_mcp.testbed.demo_server import CLEAN, POISONED


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh ingest state, a baseline with the clean tool, and alerts captured in a list."""
    clean_tool = {"name": "get_weather", "description": CLEAN, "inputSchema": {}}
    path = tmp_path / "approved-tools.json"
    path.write_text(json.dumps({"demo-mcp": {"paginated": False,
                                             "tools": core.fingerprint([clean_tool])}}))
    monkeypatch.setattr(ingest, "BASELINE_PATH", str(path))
    monkeypatch.setattr(ingest, "BASELINE_TTL", 0)
    monkeypatch.setattr(ingest, "QUARANTINE_ENABLED", False)
    monkeypatch.setattr(ingest, "BODY_PATH", ["response", "body"])
    monkeypatch.setattr(ingest, "state", ingest._State())
    sent = []
    monkeypatch.setattr(ingest, "send_alert",
                        lambda server, consumer, status, result: sent.append(
                            (server, consumer, status, result)) or True)
    return sent, path


def record(description, server="demo-mcp", consumer="agent-a"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
        {"name": "get_weather", "description": description, "inputSchema": {}}]}})
    return {"service": {"name": server}, "consumer": {"username": consumer},
            "response": {"body": body}}


def test_clean_record_does_not_alert(env):
    sent, _ = env
    out = ingest.process_record(record(CLEAN))
    assert out["status"] == "clean"
    assert sent == []


def test_changed_record_alerts_once_per_distinct_change(env):
    sent, _ = env
    first = ingest.process_record(record(POISONED))
    again = ingest.process_record(record(POISONED))
    assert first["status"] == "changed" and first["alerted"] is True
    assert again["alerted"] is False
    assert len(sent) == 1
    assert sent[0][:3] == ("demo-mcp", "agent-a", "changed")


def test_sse_body_is_understood(env):
    sent, _ = env
    rec = record(POISONED)
    rec["response"]["body"] = "event: message\ndata: " + rec["response"]["body"] + "\n\n"
    ingest.process_record(rec)
    assert len(sent) == 1


def test_non_tools_list_records_are_ignored(env):
    sent, _ = env
    rec = {"service": {"name": "demo-mcp"}, "response": {"body": '{"jsonrpc":"2.0","id":1,"result":{}}'}}
    assert ingest.process_record(rec) is None
    assert ingest.process_record({"response": {"body": ""}}) is None
    assert sent == []


def test_unapproved_server_alerts_as_unapproved(env):
    sent, _ = env
    out = ingest.process_record(record(CLEAN, server="rogue-mcp"))
    assert out["status"] == "unapproved_server"
    assert sent[0][2] == "unapproved_server"


def test_approval_stops_alerts(env):
    sent, path = env
    ingest.process_record(record(POISONED))
    assert len(sent) == 1
    approved_tool = {"name": "get_weather", "description": POISONED, "inputSchema": {}}
    path.write_text(json.dumps({"demo-mcp": {"paginated": False,
                                             "tools": core.fingerprint([approved_tool])}}))
    out = ingest.process_record(record(POISONED))
    assert out["status"] == "clean"
    assert len(sent) == 1


def test_bad_record_is_counted_not_raised(env):
    sent, _ = env
    ingest.process_record({"service": {"name": "demo-mcp"},
                           "response": {"body": '{"result": {"tools": [{"nope": 1}]}}'}})
    assert ingest.state.stats["errors"] == 1
    assert sent == []


def test_ingest_endpoint_accepts_list_and_single(env):
    from fastapi.testclient import TestClient
    client = TestClient(ingest.app)
    r = client.post("/ingest", json=[record(POISONED), record(CLEAN)])
    assert r.status_code == 200 and r.json()["checked"] == 2
    r = client.post("/ingest", json=record(POISONED))
    assert r.status_code == 200
    assert client.get("/stats").json()["records"] == 3
