"""Provider-agnostic LLM layer for the suggestor.

Builds a pydantic-ai Agent that returns a validated `SuggestionResponse`. The
provider/model come from `settings.LLMConfig`, so swapping Ollama ↔ OpenAI ↔
Anthropic ↔ Google is a config change. Provider SDKs are imported lazily so the
module loads without every backend installed.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from filer_backend.settings import LLMConfig, Settings

SYSTEM_PROMPT = """\
You organize files into a folder hierarchy. Given a file's metadata, a content
snippet, candidate folders surfaced by similarity search, and a view of the
existing folder structure, propose the best destination folders.

Rules:
- Return 1-3 ranked suggestions, most confident first, confidence in [0,1].
- Prefer existing folders when a good one exists.
- You MAY propose a folder that does not yet exist if it fits the structure
  (e.g. infer this year's "2026/Healthcare" by analogy to prior years). Set
  is_new=true and give the full absolute path.
- folder_path must be an absolute path (a directory, not a file).
- Keep rationale to one short sentence.
"""


class FolderSuggestion(BaseModel):
    folder_path: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    is_new: bool = False


class SuggestionResponse(BaseModel):
    suggestions: list[FolderSuggestion]


def build_model(llm: LLMConfig):
    provider = llm.provider.lower()
    if provider == "ollama":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIChatModel(
            llm.model,
            provider=OpenAIProvider(base_url=llm.base_url, api_key="ollama"),
        )
    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel(llm.model)
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel(llm.model)
    if provider in ("google", "gemini", "google-gla"):
        from pydantic_ai.models.google import GoogleModel

        return GoogleModel(llm.model)
    raise ValueError(f"unknown LLM provider: {llm.provider!r}")


def build_agent(settings: Settings) -> Agent[None, SuggestionResponse]:
    system = SYSTEM_PROMPT
    if settings.hints.strip():
        system += f"\n\nUser hints (treat as authoritative):\n{settings.hints.strip()}"
    return Agent(
        build_model(settings.llm),
        output_type=SuggestionResponse,
        system_prompt=system,
        model_settings={"temperature": settings.llm.temperature},
    )
