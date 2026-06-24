import argparse
import os
from typing import Any

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
    parser = argparse.ArgumentParser(description="Create a new Foundry Hosted Agent version.")
    parser.add_argument("--project-endpoint", default=env_value("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--agent-name", default=env_value("FOUNDRY_AGENT_NAME"))
    parser.add_argument("--agent-image", default=env_value("AGENT_IMAGE"))
    parser.add_argument(
        "--model-deployment",
        default=env_value("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
    )
    parser.add_argument("--cosmos-endpoint", default=env_value("COSMOS_ENDPOINT"))
    parser.add_argument("--cosmos-database-name", default=env_value("COSMOS_DATABASE_NAME"))
    parser.add_argument("--cosmos-container-name", default=env_value("COSMOS_CONTAINER_NAME"))
    parser.add_argument("--description", default="Deploy updated hosted agent image")
    return parser


def update_environment_variables(definition: dict[str, Any], args: argparse.Namespace) -> None:
    environment_variables = definition.setdefault("environment_variables", {})
    environment_variables["AZURE_AI_MODEL_DEPLOYMENT_NAME"] = required_value(
        args.model_deployment,
        "--model-deployment or AZURE_AI_MODEL_DEPLOYMENT_NAME",
    )
    environment_variables["USE_COSMOS"] = "true"
    environment_variables["COSMOS_ENDPOINT"] = required_value(
        args.cosmos_endpoint,
        "--cosmos-endpoint or COSMOS_ENDPOINT",
    )
    environment_variables["COSMOS_DATABASE_NAME"] = required_value(
        args.cosmos_database_name,
        "--cosmos-database-name or COSMOS_DATABASE_NAME",
    )
    environment_variables["COSMOS_CONTAINER_NAME"] = required_value(
        args.cosmos_container_name,
        "--cosmos-container-name or COSMOS_CONTAINER_NAME",
    )


def main() -> None:
    args = build_parser().parse_args()
    project_endpoint = required_value(
        args.project_endpoint,
        "--project-endpoint or FOUNDRY_PROJECT_ENDPOINT",
    )
    agent_name = required_value(args.agent_name, "--agent-name or FOUNDRY_AGENT_NAME")
    agent_image = required_value(args.agent_image, "--agent-image or AGENT_IMAGE")

    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
    )
    latest = list(client.agents.list_versions(agent_name, limit=1, order="desc"))[0]
    data = latest.as_dict()
    definition = data["definition"]
    definition["container_configuration"]["image"] = agent_image
    update_environment_variables(definition, args)

    body = {
        "definition": definition,
        "blueprintReference": data["blueprint_reference"],
        "description": args.description,
    }
    version = client.agents.create_version(agent_name, body=body)
    version_data = version.as_dict()
    print(
        "created",
        version_data["id"],
        version_data["status"],
        version_data["definition"]["container_configuration"]["image"],
    )


if __name__ == "__main__":
    main()
