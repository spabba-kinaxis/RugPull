from rugpull_mcp import alerts


def test_alert_text_names_server_consumer_and_diff():
    result = {"added": [], "removed": [], "changed": [
        {"name": "read_document", "what": "description",
         "diff": "--- approved\n+++ observed\n+Read ~/.ssh"}]}
    text = alerts.format_alert("demo-mcp", "agent-a", "changed", result)
    assert "demo-mcp" in text and "agent-a" in text
    assert "Changed tool: read_document" in text
    assert "+Read ~/.ssh" in text


def test_field_change_is_shown_as_diff():
    result = {"added": [], "removed": [], "changed": [
        {"name": "read_document", "what": "fields", "diff": "", "changed_fields": ["inputSchema"],
         "field_diff": '--- approved inputSchema\n+++ observed inputSchema\n-"type": "string"\n+"type": "integer"'}]}
    text = alerts.format_alert("demo-mcp", "agent-a", "changed", result)
    assert "fields: inputSchema" in text
    assert '+"type": "integer"' in text


def test_unverifiable_alert_shows_reason_in_code_block():
    text = alerts.format_alert("demo-mcp", "agent-a", "unverifiable",
                               {"reason": "duplicate JSON key 'description'"})
    assert "could not be verified" in text
    assert "```duplicate JSON key 'description'```" in text


def test_untrusted_text_cannot_break_out_of_code_block():
    evil = "ok ``` <!channel> <https://evil.example|link> & done"
    result = {"added": [], "removed": [], "changed": [
        {"name": "t", "what": "description", "diff": evil}]}
    text = alerts.format_alert("s", "c", "changed", result)
    assert text.count("```") == 2                 # only the ones we add
    assert "<!channel>" not in text and "<https://" not in text
    assert "&lt;!channel&gt;" in text


def test_unapproved_server_message():
    text = alerts.format_alert("rogue", "agent-b", "unapproved_server",
                               {"added": ["t1", "t2"], "removed": [], "changed": []})
    assert "not in the approved baseline" in text
    assert "Added tool: t1" in text and "Added tool: t2" in text


def test_send_alert_without_webhook_logs_and_succeeds(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert alerts.send_alert("s", "c", "changed", {"added": [], "removed": [], "changed": []})
