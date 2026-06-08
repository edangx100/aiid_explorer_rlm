from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    # .env is optional — missing file is silently ignored; env vars always take precedence
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Required — no default forces a ValidationError if absent
    OPENROUTER_API_KEY: str
    OPENROUTER_MODEL: str = "minimax/minimax-m3"
    # Inner model is configurable independently so cheap/fast models can classify while outer reasons
    OPENROUTER_INNER_MODEL: str = "minimax/minimax-m3"
    BRAINTRUST_API_KEY: str = ""
    BRAINTRUST_PROJECT: str = "aiid-explorer"
    MAX_ROUNDS: int = 10
    # Set to None (via STEERING_ROUND=none in .env) to keep all rounds on the verbatim user query
    STEERING_ROUND: Optional[int] = 5
    STOP_AFTER_N_CALLS: int = 5
    MAX_VALIDATOR_RETRIES: int = 3
    AIID_RESULTS_LIMIT: int = 20

    @field_validator("STEERING_ROUND", mode="before")
    @classmethod
    def parse_steering_round(cls, v):
        # Env vars arrive as strings; "none" (any case) maps to Python None to disable steering
        if isinstance(v, str) and v.lower() == "none":
            return None
        return v


settings = Settings()
