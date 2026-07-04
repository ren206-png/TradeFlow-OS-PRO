import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Plan limits — override any value via env vars at runtime:
#   PLAN_STARTER_CALLS=20  PLAN_STARTER_SMS=50
#   PLAN_PRO_CALLS=300     PLAN_PRO_SMS=600
# ---------------------------------------------------------------------------
PLAN_LIMITS = {
    "starter": {
        "calls":          int(os.environ.get("PLAN_STARTER_CALLS", 100)),
        "sms":            int(os.environ.get("PLAN_STARTER_SMS",   200)),
        "max_call_mins":  int(os.environ.get("PLAN_STARTER_MAX_CALL_MINS", 10)),
        "price_id":       os.environ.get("STRIPE_STARTER_PRICE_ID", "price_starter"),
    },
    "pro": {
        "calls":          int(os.environ.get("PLAN_PRO_CALLS", 500)),
        "sms":            int(os.environ.get("PLAN_PRO_SMS",   1000)),
        "max_call_mins":  int(os.environ.get("PLAN_PRO_MAX_CALL_MINS", 30)),
        "price_id":       os.environ.get("STRIPE_PRO_PRICE_ID", "price_pro"),
    },
    "enterprise": {
        "calls":          int(os.environ.get("PLAN_ENTERPRISE_CALLS", 9999)),
        "sms":            int(os.environ.get("PLAN_ENTERPRISE_SMS",   9999)),
        "max_call_mins":  int(os.environ.get("PLAN_ENTERPRISE_MAX_CALL_MINS", 60)),
        "price_id":       os.environ.get("STRIPE_ENTERPRISE_PRICE_ID", "price_enterprise"),
    },
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

    # Admin dashboard credentials (set these in Railway to change login)
    admin_username: str = "admin"
    admin_password: str = ""  # falls back to secret_key if empty
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

    # Mailchimp — optional, drip emails disabled if empty
    mailchimp_api_key: str = ""
    mailchimp_server_prefix: str = ""   # e.g. "us1", "us14"
    mailchimp_list_id: str = ""         # Audience ID


settings = Settings()
