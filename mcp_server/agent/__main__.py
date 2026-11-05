"""Run the local agent.

    # one question
    python -m agent --mcp-url http://127.0.0.1:8501/mcp/demo --consumer agent-a \
        --prompt "Summarise the document notes/q3.md"

    # interactive chat (empty line or Ctrl-D to quit)
    python -m agent --mcp-url http://127.0.0.1:8501/mcp/demo --consumer agent-a

The model is AGENT_MODEL (default claude-haiku-5-5). The API key is read by the
Anthropic SDK from ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .loop import Agent


async def chat(args: argparse.Namespace) -> int:
    from .llm import AnthropicLlm
    from .mcp_client import connect

    llm = AnthropicLlm()
    token = os.environ.get("MCP_TOKEN", "")
    async with connect(args.mcp_url, args.consumer, token) as tools:
        agent = Agent(llm=llm, mcp=tools)
        if args.prompt:
            answer = await agent.run(args.prompt)
            print(answer)
            if args.verbose:
                print("\n".join(f"  [trace] {line}" for line in agent.trace), file=sys.stderr)
            return 0
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                print()
                return 0
            if not line:
                return 0
            print(f"agent> {await agent.run(line)}")
            if args.verbose:
                print("\n".join(f"  [trace] {t}" for t in agent.trace[-5:]), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local MCP agent.")
    ap.add_argument("--mcp-url", default="http://127.0.0.1:8501/mcp/demo",
                    help="MCP endpoint, usually through the gateway")
    ap.add_argument("--consumer", default="agent-a", help="consumer name sent as X-Consumer")
    ap.add_argument("--prompt", help="ask one question and exit")
    ap.add_argument("--verbose", action="store_true", help="print the tool trace to stderr")
    args = ap.parse_args(argv)
    try:
        return asyncio.run(chat(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
