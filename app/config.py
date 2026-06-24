from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    menu_data_path: Path = Path("data/menu.json")
    use_cosmos: bool = False

    cosmos_endpoint: str | None = None
    cosmos_database_name: str = "dinner-planner"
    cosmos_container_name: str = "menus"

    foundry_project_endpoint: str | None = None
    foundry_agent_name: str | None = None

    max_search_items: int = Field(default=10, ge=1, le=20)


def get_settings() -> Settings:
    return Settings()
