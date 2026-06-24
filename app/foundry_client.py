from azure.identity import DefaultAzureCredential

from app.config import Settings


class FoundryHostedAgentClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.foundry_project_endpoint and self._settings.foundry_agent_name)

    async def get_response(self, message: str, thread_id: str | None) -> tuple[str, str | None]:
        if not self.is_configured:
            raise RuntimeError("Foundry Hosted Agent is not configured")

        from agent_framework.foundry import FoundryAgent

        agent = FoundryAgent(
            agent_name=self._settings.foundry_agent_name,
            project_endpoint=self._settings.foundry_project_endpoint,
            credential=DefaultAzureCredential(),
            allow_preview=True,
        )
        response = await agent.run(message)
        response_text = str(getattr(response, "text", response))
        response_thread_id = getattr(response, "response_id", thread_id)
        return response_text, response_thread_id
