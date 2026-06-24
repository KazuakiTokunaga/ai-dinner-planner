from agent_framework_foundry_hosting import ResponsesHostServer

from agent.hosted_agent import create_agent


def main() -> None:
    server = ResponsesHostServer(create_agent())
    server.run()


if __name__ == "__main__":
    main()
