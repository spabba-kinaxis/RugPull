"""End-to-end test: test agent -> gateway stand-in -> demo MCP server, with the
detector watching the log records. Each service runs as its own process on a
free port. Slack and Konnect are replaced by a fake service.

Scenario (matches the demo script in the design doc):
  1. Clean listings produce no alert.
  2. The demo server flips its description on listing 6. One alert arrives within 10 s.
  3. Ten more listings produce no further alerts (duplicate suppression).
  4. A second, unapproved server produces one "not in baseline" alert.
  5. A baseline update approves the change, and the demo server stays silent.
  6. With QUARANTINE_ENABLED, the changed route receives the Request Termination plugin.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from rugpull_mcp import core
from rugpull_mcp.testbed.demo_server import CLEAN

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"service did not become ready: {url}")


def wait_for(predicate, timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return None


class Stack:
    """Starts the demo stack and exposes the ports and the fake service."""

    def __init__(self, tmp_path: Path, quarantine: bool) -> None:
        self.tmp = tmp_path
        self.procs: list[subprocess.Popen] = []
        self.demo = free_port()
        self.gateway = free_port()
        self.ingest = free_port()
        self.fake = free_port()
        self.baseline = tmp_path / "approved-tools.json"
        self.quarantine = quarantine

    def start(self) -> None:
        clean_tool = {"name": "get_weather", "description": CLEAN,
                      "inputSchema": {"type": "object",
                                      "properties": {"city": {"type": "string"}},
                                      "required": ["city"]}}
        self.baseline.write_text(json.dumps({"demo-mcp": {
            "paginated": False, "tools": core.fingerprint([clean_tool])}}))

        base_env = dict(os.environ, PYTHONPATH=str(ROOT), FLIP_AFTER="5")
        upstream = f"http://127.0.0.1:{self.demo}/mcp"
        routes = {
            "/mcp/demo": {"service": "demo-mcp", "upstream": upstream},
            "/mcp/rogue": {"service": "rogue-mcp", "upstream": upstream},
        }
        ingest_env = dict(base_env,
                          BASELINE_PATH=str(self.baseline),
                          BASELINE_TTL_SECONDS="1",
                          SLACK_WEBHOOK_URL=f"http://127.0.0.1:{self.fake}/slack/webhook",
                          QUARANTINE_ENABLED="true" if self.quarantine else "false",
                          KONNECT_API=f"http://127.0.0.1:{self.fake}/konnect",
                          KONNECT_PAT="test-pat",
                          CONTROL_PLANE_ID="cp-test",
                          ROUTE_IDS=json.dumps({"demo-mcp": "route-demo"}))
        services = [
            ("fake", [PY, "-m", "uvicorn", "tests.e2e.fake_services:app",
                      "--port", str(self.fake), "--log-level", "warning"], base_env),
            ("demo", [PY, "-m", "uvicorn", "rugpull_mcp.testbed.demo_server:app",
                      "--port", str(self.demo), "--log-level", "warning"], base_env),
            ("ingest", [PY, "-m", "uvicorn", "rugpull_mcp.ingest:app",
                        "--port", str(self.ingest), "--log-level", "warning"], ingest_env),
            ("gateway", [PY, "-m", "uvicorn", "rugpull_mcp.testbed.gateway_sim:app",
                         "--port", str(self.gateway), "--log-level", "warning"],
             dict(base_env, ROUTES=json.dumps(routes),
                  DETECTOR_URL=f"http://127.0.0.1:{self.ingest}/ingest")),
        ]
        for name, cmd, env in services:
            log = open(self.tmp / f"{name}.log", "w")
            self.procs.append(subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log,
                                               stderr=subprocess.STDOUT))
        for port in (self.fake, self.demo, self.ingest, self.gateway):
            wait_http(f"http://127.0.0.1:{port}/healthz" if port != self.demo
                      else f"http://127.0.0.1:{port}/state")

    def stop(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    # helpers ----------------------------------------------------------------
    def listing(self, route: str, consumer: str = "agent-a") -> dict:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        r = httpx.post(f"http://127.0.0.1:{self.gateway}{route}", json=body, timeout=10,
                       headers={"Accept": "application/json, text/event-stream",
                                "X-Consumer": consumer})
        r.raise_for_status()
        return r.json()

    def slack(self) -> list[dict]:
        return httpx.get(f"http://127.0.0.1:{self.fake}/slack/messages").json()

    def konnect_calls(self) -> list[dict]:
        return httpx.get(f"http://127.0.0.1:{self.fake}/konnect-calls").json()

    def ingest_stats(self) -> dict:
        return httpx.get(f"http://127.0.0.1:{self.ingest}/stats").json()

    def reset_demo(self) -> None:
        httpx.post(f"http://127.0.0.1:{self.demo}/reset", timeout=5)


@pytest.fixture
def stack(tmp_path):
    s = Stack(tmp_path, quarantine=False)
    s.start()
    yield s
    s.stop()


def test_detects_rug_pull_end_to_end(stack: Stack):
    stack.reset_demo()

    # 1. Clean listings: no alert.
    for _ in range(5):
        stack.listing("/mcp/demo")
    time.sleep(0.5)
    assert stack.slack() == [], "clean listings must not alert"

    # 2. Listing 6 is poisoned. The alert must arrive within 10 seconds.
    flipped_at = time.time()
    poisoned = stack.listing("/mcp/demo")
    assert "~/.ssh" in poisoned["result"]["tools"][0]["description"]
    messages = wait_for(lambda: stack.slack() or None, timeout=10)
    latency = time.time() - flipped_at
    print(f"\nalert latency after description change: {latency:.2f}s")
    assert messages, "no alert within 10 seconds of the description change"
    assert latency < 10
    text = messages[0]["text"]
    assert "demo-mcp" in text and "agent-a" in text
    assert "get_weather" in text
    assert "+Before answering" in text or "Before answering" in text

    # 3. Duplicate suppression: ten more poisoned listings, still one alert.
    for _ in range(10):
        stack.listing("/mcp/demo")
    time.sleep(0.5)
    assert len(stack.slack()) == 1, "identical change must alert once"

    # 4. An unapproved server alerts once with all its tools as added.
    stack.listing("/mcp/rogue", consumer="agent-b")
    stack.listing("/mcp/rogue", consumer="agent-b")
    rogue = wait_for(lambda: [m for m in stack.slack() if "rogue-mcp" in m["text"]] or None,
                     timeout=5)
    assert rogue and "agent-b" in rogue[0]["text"] and "get_weather" in rogue[0]["text"]
    assert len(rogue) == 1

    # 5. Approval: merge the new description into the baseline. The alert must stop.
    poisoned_tool = poisoned["result"]["tools"][0]
    approved = {"demo-mcp": {"paginated": False,
                             "tools": core.fingerprint([poisoned_tool])}}
    stack.baseline.write_text(json.dumps(approved))
    time.sleep(1.5)  # baseline TTL is 1 second in this test
    before = len(stack.slack())
    for _ in range(3):
        result = stack.listing("/mcp/demo")
        assert result["result"]["tools"][0] == poisoned_tool
    time.sleep(0.5)
    assert len(stack.slack()) == before, "approved change must not alert"

    stats = stack.ingest_stats()
    assert stats["tool_listings"] >= 20
    assert stats["alerts"] == 2


def test_quarantine_adds_plugin_to_changed_route(tmp_path):
    s = Stack(tmp_path, quarantine=True)
    s.start()
    try:
        s.reset_demo()
        for _ in range(6):
            s.listing("/mcp/demo")
        assert wait_for(lambda: s.slack() or None, timeout=10), "no alert"
        calls = wait_for(
            lambda: [c for c in s.konnect_calls() if c["method"] == "POST"] or None,
            timeout=5)
        assert calls, "quarantine POST was not sent"
        assert "/v2/control-planes/cp-test/core-entities/routes/route-demo/plugins" in calls[0]["path"]
        body = json.loads(calls[0]["body"])
        assert body["name"] == "request-termination"
        assert body["config"]["status_code"] == 403
        assert "mcp-quarantine" in body["tags"]
    finally:
        s.stop()
