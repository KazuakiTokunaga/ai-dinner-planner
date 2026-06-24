import json
from pathlib import Path

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.cosmos_endpoint is None:
        raise ValueError("COSMOS_ENDPOINT is required")

    client = CosmosClient(settings.cosmos_endpoint, credential=DefaultAzureCredential())
    database = client.get_database_client(settings.cosmos_database_name)
    container = database.get_container_client(settings.cosmos_container_name)

    menus = json.loads(Path(settings.menu_data_path).read_text(encoding="utf-8"))
    for menu in menus:
        container.upsert_item(menu)
        print(f"upserted {menu['id']}")


if __name__ == "__main__":
    main()
