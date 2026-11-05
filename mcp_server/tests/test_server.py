import json

import pytest

from rugpull_mcp import server

from .conftest import CLEAN, POISONED, rpc_body, weather_tool


def test_clean_listing_is_clean(baseline_file):
    out = server.compare_tool_listing("demo-mcp", rpc_body([weather_tool()]),
                                      consumer="agent-a")
    assert out["status"] == "clean"
    assert out["alert"] is False
    assert out["consumer"] == "agent-a"
    assert out["added"] == out["removed"] == out["changed"] == []


def test_poisoned_listing_alerts_with_diff(baseline_file):
    out = server.compare_tool_listing(
        "demo-mcp", rpc_body([weather_tool(description=POISONED)]),
        consumer="agent-a")
    assert out["status"] == "changed"
    assert out["alert"] is True
    assert out["server"] == "demo-mcp"
    assert out["consumer"] == "agent-a"
    change = out["changed"][0]
    assert change["name"] == "get_weather"
    assert "+Before answering" in change["diff"]


def test_unapproved_server_lists_all_tools_as_added(baseline_file):
    out = server.compare_tool_listing("other-mcp", rpc_body([weather_tool()]))
    assert out["status"] == "unapproved_server"
    assert out["added"] == ["get_weather"]


def test_missing_baseline_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BASELINE_PATH", tmp_path / "missing.json")
    with pytest.raises(ValueError, match="not available"):
        server.compare_tool_listing("demo-mcp", rpc_body([weather_tool()]))


def test_fingerprint_tools_reports_hashes():
    out = server.fingerprint_tools(rpc_body([weather_tool()]))
    assert out["tool_count"] == 1
    assert len(out["tools"]["get_weather"]["hash"]) == 64


def test_fingerprint_accepts_parsed_dict():
    out = server.fingerprint_tools({"tools": [weather_tool()]})
    assert out["tool_count"] == 1


def test_build_baseline_entry_shows_change_from_current(baseline_file):
    out = server.build_baseline_entry("demo-mcp",
                                      rpc_body([weather_tool(description=POISONED)]))
    assert out["current_baseline"] == "present"
    assert [c["name"] for c in out["change_from_current"]["changed"]] == ["get_weather"]
    assert out["entry"]["paginated"] is False
    assert set(out["entry"]["tools"]) == {"get_weather"}


def test_build_baseline_entry_for_new_server(baseline_file):
    out = server.build_baseline_entry("new-mcp", rpc_body([weather_tool()]),
                                      paginated=True)
    assert out["current_baseline"] == "unapproved_server"
    assert out["change_from_current"]["added"] == ["get_weather"]
    assert out["entry"]["paginated"] is True


def test_server_name_validation(baseline_file):
    with pytest.raises(ValueError):
        server.compare_tool_listing("   ", rpc_body([weather_tool()]))
    with pytest.raises(ValueError):
        server.compare_tool_listing("x" * 200, rpc_body([weather_tool()]))


def test_baseline_is_not_written(baseline_file):
    before = baseline_file.read_text()
    server.build_baseline_entry("demo-mcp", rpc_body([weather_tool(description=POISONED)]))
    server.compare_tool_listing("demo-mcp", rpc_body([weather_tool(description=POISONED)]))
    assert baseline_file.read_text() == before
