"""A local agent that uses MCP tools through the gateway.

The agent loop (loop.py) does not depend on any network client, so it can be
tested with fakes. The Anthropic and MCP adapters are in llm.py and mcp_client.py.
"""
