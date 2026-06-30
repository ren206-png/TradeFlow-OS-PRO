from pydantic_settings import BaseSettings, SettingsConfigDict

PLAN_LIMITS = {
    "starter":    {"calls": 100,  "sms": 200,  "price_id": "price_starter"},
    "pro":        {"calls": 500,  "sms": 1000, "price_id": "price_pro"},
    "enterprise": {"calls": 9999, "sms": 9999, "price_id": "price_enterprise"},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str
    retell_api_key: str
    retell_webhook_secret: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    database_url: str
    secret_key: str
    debug: bool = False
    claude_model: str = "claude-sonnet-4-6"
    claude_max_tokens: int = 1024
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_pro_price_id: str = ""

    # Email (SMTP) — optional, notifications disabled if empty
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@tradesflowos.com"


settings = Settings()
