import json
from pathlib import Path
from typing import Protocol

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from app.config import Settings
from app.menu_models import Menu, MenuSearchCriteria


class MenuRepository(Protocol):
    def search_menus(self, criteria: MenuSearchCriteria) -> list[Menu]: ...

    def get_menus(self, menu_ids: list[str]) -> list[Menu]: ...


class LocalJsonMenuRepository:
    def __init__(self, data_path: Path) -> None:
        self._data_path = data_path
        self._menus: list[Menu] | None = None

    def search_menus(self, criteria: MenuSearchCriteria) -> list[Menu]:
        menus = self._load_menus()
        results = [menu for menu in menus if self._matches(menu, criteria)]
        return results[: criteria.max_items]

    def get_menus(self, menu_ids: list[str]) -> list[Menu]:
        requested_ids = set(menu_ids)
        return [menu for menu in self._load_menus() if menu.id in requested_ids]

    def _load_menus(self) -> list[Menu]:
        if self._menus is None:
            raw_menus = json.loads(self._data_path.read_text(encoding="utf-8"))
            self._menus = [Menu.model_validate(raw_menu) for raw_menu in raw_menus]
        return self._menus

    @staticmethod
    def _matches(menu: Menu, criteria: MenuSearchCriteria) -> bool:
        if criteria.main_ingredient and criteria.main_ingredient not in menu.main_ingredient:
            return False
        if criteria.category and criteria.category not in menu.category:
            return False
        if criteria.cook_with and criteria.cook_with not in menu.cook_with:
            return False
        return True


class CosmosMenuRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.cosmos_endpoint is None:
            raise ValueError("COSMOS_ENDPOINT is required when USE_COSMOS=true")

        credential = DefaultAzureCredential()
        client = CosmosClient(settings.cosmos_endpoint, credential=credential)
        database = client.get_database_client(settings.cosmos_database_name)
        self._container = database.get_container_client(settings.cosmos_container_name)

    def search_menus(self, criteria: MenuSearchCriteria) -> list[Menu]:
        clauses: list[str] = []
        parameters: list[dict[str, str | int]] = []

        if criteria.main_ingredient:
            clauses.append("CONTAINS(c.main_ingredient, @main_ingredient)")
            parameters.append({"name": "@main_ingredient", "value": criteria.main_ingredient})
        if criteria.category:
            clauses.append("CONTAINS(c.category, @category)")
            parameters.append({"name": "@category", "value": criteria.category})
        if criteria.cook_with:
            clauses.append("CONTAINS(c.cook_with, @cook_with)")
            parameters.append({"name": "@cook_with", "value": criteria.cook_with})

        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM c{where_clause} OFFSET 0 LIMIT @max_items"
        parameters.append({"name": "@max_items", "value": criteria.max_items})

        items = self._container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
        return [Menu.model_validate(item) for item in items]

    def get_menus(self, menu_ids: list[str]) -> list[Menu]:
        if not menu_ids:
            return []

        items = self._container.query_items(
            query="SELECT * FROM c WHERE ARRAY_CONTAINS(@menu_ids, c.id)",
            parameters=[{"name": "@menu_ids", "value": menu_ids}],
            enable_cross_partition_query=True,
        )
        return [Menu.model_validate(item) for item in items]


def create_menu_repository(settings: Settings) -> MenuRepository:
    if settings.use_cosmos:
        return CosmosMenuRepository(settings)
    return LocalJsonMenuRepository(settings.menu_data_path)
