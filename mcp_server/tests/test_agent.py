import asyncio

import pytest

from agent.loop import SYSTEM_PROMPT, Agent, AgentError, ToolError

WEATHER = {"name": "get_weather", "description": "Returns the weather.",
           "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}}


class FakeMcp:
    def __init__(self, tools=(WEATHER,), fail=False):
        self.tools = list(tools)
        self.calls = []
        self.fail = fail

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail:
            raise ToolError("upstream exploded")
        return f"Sunny in {arguments.get('city')}"


class ScriptedLlm:
    """Returns the scripted replies in order and records what it was shown."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def respond(self, system, messages, tools):
        self.seen.append({"system": system, "messages": list(messages), "tools": tools})
        return self.replies.pop(0)


def tool_use(block_id, name, args):
    return {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": block_id, "name": name, "input": args}]}


def text(answer):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": answer}]}


def run(agent, prompt="hi"):
    return asyncio.run(agent.run(prompt))


def test_answers_directly_when_no_tool_is_needed():
    llm = ScriptedLlm([text("Hello.")])
    assert run(Agent(llm=llm, mcp=FakeMcp())) == "Hello."


def test_calls_the_chosen_tool_and_returns_its_result_to_the_model():
    mcp = FakeMcp()
    llm = ScriptedLlm([tool_use("t1", "get_weather", {"city": "Oslo"}), text("It is sunny.")])
    agent = Agent(llm=llm, mcp=mcp)
    assert run(agent, "weather in Oslo?") == "It is sunny."
    assert mcp.calls == [("get_weather", {"city": "Oslo"})]
    tool_result = llm.seen[1]["messages"][-1]["content"][0]
    assert tool_result == {"type": "tool_result", "tool_use_id": "t1", "content": "Sunny in Oslo"}


def test_model_is_given_the_listed_tools_in_api_format():
    llm = ScriptedLlm([text("ok")])
    run(Agent(llm=llm, mcp=FakeMcp()))
    assert llm.seen[0]["tools"] == [{"name": "get_weather", "description": "Returns the weather.",
                                     "input_schema": WEATHER["inputSchema"]}]
    assert llm.seen[0]["system"] == SYSTEM_PROMPT


def test_refuses_a_tool_that_was_not_listed():
    mcp = FakeMcp()
    llm = ScriptedLlm([tool_use("t1", "delete_everything", {}), text("I could not do that.")])
    agent = Agent(llm=llm, mcp=mcp)
    run(agent)
    assert mcp.calls == []
    result = llm.seen[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "not available" in result["content"]


def test_tool_errors_go_back_to_the_model_not_up_the_stack():
    llm = ScriptedLlm([tool_use("t1", "get_weather", {"city": "x"}), text("The tool failed.")])
    agent = Agent(llm=llm, mcp=FakeMcp(fail=True))
    assert run(agent) == "The tool failed."
    result = llm.seen[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "upstream exploded" in result["content"]


def test_stops_after_max_turns():
    llm = ScriptedLlm([tool_use(f"t{i}", "get_weather", {"city": "x"}) for i in range(3)])
    with pytest.raises(AgentError):
        run(Agent(llm=llm, mcp=FakeMcp(), max_turns=3))


def test_trace_records_what_happened():
    llm = ScriptedLlm([tool_use("t1", "get_weather", {"city": "Oslo"}), text("done")])
    agent = Agent(llm=llm, mcp=FakeMcp())
    run(agent)
    assert agent.trace[0] == "tools available: ['get_weather']"
    assert any(line.startswith("call get_weather") for line in agent.trace)
