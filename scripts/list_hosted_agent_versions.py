import argparse
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def env_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


def required_value(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List Foundry Hosted Agent versions.")
    parser.add_argument("--project-endpoint", default=env_value("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--agent-name", default=env_value("FOUNDRY_AGENT_NAME"))
    parser.add_argument("--limit", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_endpoint = required_value(
        args.project_endpoint,
        "--project-endpoint or FOUNDRY_PROJECT_ENDPOINT",
    )
    agent_name = required_value(args.agent_name, "--agent-name or FOUNDRY_AGENT_NAME")

    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
    )
    for index, version in enumerate(client.agents.list_versions(agent_name, order="desc")):
        if index >= args.limit:
            break
        data = version.as_dict()
        print(
            data["version"],
            data["status"],
            data["definition"]["container_configuration"]["image"],
        )


if __name__ == "__main__":
    main()
