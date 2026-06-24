import argparse
import asyncio

from agent_framework_foundry_hosting import ResponsesHostServer

from agent.hosted_agent import create_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AI Dinner Planner hosted agent.")
    parser.add_argument(
        "--local-chat",
        action="store_true",
        help="Run a local interactive chat loop instead of the Hosted Agent server.",
    )
    return parser


async def run_local_chat() -> None:
    agent = create_agent()
    print("ローカルチャットを開始します。終了するには exit または quit を入力してください。")

    while True:
        message = input("You> ").strip()
        if message.lower() in {"exit", "quit"}:
            break
        if not message:
            continue

        response = await agent.run(message)
        print("Agent>", getattr(response, "text", response))


def main() -> None:
    args = build_parser().parse_args()
    if args.local_chat:
        asyncio.run(run_local_chat())
        return

    server = ResponsesHostServer(create_agent())
    server.run()


if __name__ == "__main__":
    main()
