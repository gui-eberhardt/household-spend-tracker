from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Tried in order when the main model is busy or retired (comma-separated)
    gemini_fallback_models: str = "gemini-3.5-flash-lite"

    household_password: str
    household_members: str = "Guilherme,Lazia"
    secret_key: str

    # Fuzzy-matching thresholds (0-100)
    alias_match_threshold: float = 92
    product_match_threshold: float = 90

    max_upload_mb: int = 8
    cookie_secure: bool = True

    @property
    def members(self) -> list[str]:
        return [m.strip() for m in self.household_members.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
