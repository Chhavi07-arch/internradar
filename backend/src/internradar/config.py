"""Runtime settings (env vars) plus the user's search config and company watchlist."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTERNRADAR_", env_file=REPO_ROOT / ".env")

    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"
    # Claude model for reading emails. claude-haiku-4-5 is ~5x cheaper if accuracy holds.
    llm_model: str = "claude-opus-5"
    # Read from the environment or the project's .env file; never committed.
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "INTERNRADAR_ANTHROPIC_API_KEY"),
    )

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'internradar.db'}"


class Candidate(BaseModel):
    batch: int
    degree: str


class Locations(BaseModel):
    cities: list[str] = Field(default_factory=list)
    remote: bool = True


class SearchConfig(BaseModel):
    candidate: Candidate
    roles: list[str]
    preferred_roles: list[str] = Field(default_factory=list)
    locations: Locations
    max_age_hours: int = 72
    exclude_companies: list[str] = Field(default_factory=list)


class WatchedCompany(BaseModel):
    name: str
    ats: Literal["greenhouse", "lever", "ashby"]
    slug: str
    tags: list[str] = Field(default_factory=list)  # e.g. ["ai", "funded-2026"]
    funding: str | None = None  # latest round, shown on the dashboard


class Watchlist(BaseModel):
    companies: list[WatchedCompany] = Field(default_factory=list)


class GmailAccount(BaseModel):
    label: str = Field(pattern=r"^[a-z0-9_-]+$")
    query: str
    trusted: bool = False


class GmailConfig(BaseModel):
    lookback_days: int = 30
    accounts: list[GmailAccount] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_search_config(path: Path | None = None) -> SearchConfig:
    path = path or get_settings().config_dir / "search.yaml"
    with path.open() as f:
        return SearchConfig.model_validate(yaml.safe_load(f))


def load_watchlist(path: Path | None = None) -> Watchlist:
    path = path or get_settings().config_dir / "watchlist.yaml"
    if not path.exists():
        return Watchlist()
    with path.open() as f:
        return Watchlist.model_validate(yaml.safe_load(f) or {})


def load_gmail_config(path: Path | None = None) -> GmailConfig:
    path = path or get_settings().config_dir / "gmail.yaml"
    if not path.exists():
        return GmailConfig()
    with path.open() as f:
        return GmailConfig.model_validate(yaml.safe_load(f) or {})
