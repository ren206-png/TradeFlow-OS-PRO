from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str
    retell_api_key: str
    retell_webhook_secret: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str
    database_url: str
    secret_key: str
    debug: bool = False
    claude_model: str = "claude-sonnet-4-6"
    claude_max_tokens: int = 1024


settings = Settings()
