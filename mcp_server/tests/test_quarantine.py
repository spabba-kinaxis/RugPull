import json

import pytest

from rugpull_mcp import quarantine as q


class FakeResp:
    def __init__(self, payload=None):
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("KONNECT_API", "https://api.example")
    monkeypatch.setenv("CONTROL_PLANE_ID", "cp1")
    monkeypatch.setenv("KONNECT_PAT", "pat")
    monkeypatch.setenv("ROUTE_IDS", json.dumps({"demo-mcp": "route-1"}))
    return q.QuarantineConfig()


def test_quarantine_posts_request_termination(monkeypatch, cfg):
    calls = []
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResp({"data": []}))

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        return FakeResp({"id": "p1"})

    monkeypatch.setattr(q.requests, "post", fake_post)
    assert q.quarantine("demo-mcp", cfg) == "p1"
    url, body = calls[0]
    assert url == "https://api.example/v2/control-planes/cp1/core-entities/routes/route-1/plugins"
    assert body["name"] == "request-termination"
    assert body["config"]["status_code"] == 403
    assert body["tags"] == ["mcp-quarantine"]


def test_quarantine_is_idempotent(monkeypatch, cfg):
    existing = {"data": [{"id": "p0", "tags": ["mcp-quarantine"]}]}
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResp(existing))
    monkeypatch.setattr(q.requests, "post", lambda *a, **k: pytest.fail("should not post"))
    assert q.quarantine("demo-mcp", cfg) == "p0"


def test_release_deletes_only_quarantine_plugins(monkeypatch, cfg):
    listing = {"data": [{"id": "p1", "tags": ["mcp-quarantine"]},
                        {"id": "other", "tags": ["team-x"]}]}
    deleted = []
    monkeypatch.setattr(q.requests, "get", lambda *a, **k: FakeResp(listing))
    monkeypatch.setattr(q.requests, "delete",
                        lambda url, headers, timeout: deleted.append(url) or FakeResp())
    assert q.release("demo-mcp", cfg) == 1
    assert deleted == ["https://api.example/v2/control-planes/cp1/core-entities/routes/route-1/plugins/p1"]


def test_unknown_server_is_an_error(cfg):
    with pytest.raises(KeyError):
        cfg.route_url("nope")
