"""Runtime configuration for the recommendation engine.

Loaded (lowest→highest precedence) from defaults → a user TOML file at
`config.config_file()` → environment variables (prefix `FILER_`, nested with
`__`, e.g. `FILER_LLM__PROVIDER=openai`).

An `apps/backend/.env` file is loaded into the process environment at import
(via python-dotenv, without overriding already-set vars). That populates both
the `FILER_*` settings below and the provider SDK keys (`OPENAI_API_KEY`, …),
which the SDKs read straight from `os.environ`.

Named `profiles` let us swap LLMs for experiments; profile names line up with
evaluation experiment labels (see filer_backend/eval).
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from filer_backend.config import config_file

# Load apps/backend/.env (resolved absolutely so it works regardless of CWD)
# before any Settings() is constructed. override=False keeps real exported
# env vars authoritative over the file.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class LLMConfig(BaseModel):
    provider: str = "ollama"  # ollama | openai | anthropic | google
    model: str = "llama3.1"
    temperature: float = 0.0
    # For Ollama / OpenAI-compatible endpoints. API providers read their key
    # from the standard env var (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...).
    base_url: str | None = "http://localhost:11434/v1"


class EmbeddingConfig(BaseModel):
    model: str = "BAAI/bge-small-en-v1.5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FILER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    # Free-text guidance injected into the suggestor's system prompt.
    hints: str = ""
    # Named LLM profiles for experiments, e.g. {"openai": {provider, model}}.
    profiles: dict[str, LLMConfig] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: init > env (incl. loaded .env) > TOML file > defaults.
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_file()),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def settings_for_profile(profile: str | None) -> Settings:
    """Return settings with `llm` swapped to the named profile (for experiments)."""
    base = get_settings()
    if not profile:
        return base
    if profile not in base.profiles:
        raise KeyError(f"unknown LLM profile: {profile!r} (have {list(base.profiles)})")
    return base.model_copy(update={"llm": base.profiles[profile]})
