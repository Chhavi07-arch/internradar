"""Runtime settings (env vars) and the user's search config (config/search.yaml)."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTERNRADAR_", env_file=REPO_ROOT / ".env")

    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_search_config(path: Path | None = None) -> SearchConfig:
    path = path or get_settings().config_dir / "search.yaml"
    with path.open() as f:
        return SearchConfig.model_validate(yaml.safe_load(f))
