from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv

from sre_agent.core.agent import PydanticSreAgent
from sre_agent.core.coral_mcp import CoralMcpClient


async def _ask(prompt: str) -> str:
    agent = PydanticSreAgent()
    return await agent.answer(prompt)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run the Coral AI SRE agent locally.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("doctor", help="Check Coral MCP connectivity.")

    ask = subparsers.add_parser("ask", help="Ask the SRE agent a question.")
    ask.add_argument("prompt", nargs="+")

    args = parser.parse_args()

    if args.command == "doctor":
        print(CoralMcpClient(os.getenv("CORAL_BIN", "coral")).smoke_test_sync())
        return

    if args.command == "ask":
        print(asyncio.run(_ask(" ".join(args.prompt))))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
