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
    twilio_messaging_service_sid: str = ""  # A2P 10DLC — set this in Railway
    database_url: str
    secret_key: str
    debug: bool = False

    # Admin dashboard credentials (set these in Railway to change login)
    admin_username: str = "admin"
    admin_password: str = ""  # falls back to secret_key if empty

    # Live demo line — set in Railway after provisioning the demo tenant
    demo_phone_number: str = ""        # rendered on marketing site hero
    demo_contractor_id: str = ""       # UUID of the demo Contractor row
    demo_daily_call_cap: int = 50      # max demo calls per day
    demo_max_call_mins: int = 3        # max 3 min per demo call
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

    # ── Feature flags ──────────────────────────────────────────────────────────
    # Toggle via Railway env vars (TRUST_V2=true, MOBILE_HERO_V2=true, etc.)
    # All default OFF so production is stable until you flip the switch.
    trust_v2: bool = False          # Replace unverified testimonials with founder block
    mobile_hero_v2: bool = False    # Enhanced mobile hero layout
    live_metrics: bool = False      # Show live DB-backed stats on landing page

    # Multi-language support — set MULTILANG_ENABLED=true in Railway to activate.
    # With flag off (default) behavior is byte-for-byte identical to pre-multilang.
    multilang_enabled: bool = False
    # Voice used when multilang is on. Must support multilingual synthesis in Retell.
    # Override via MULTILANG_VOICE_ID env var if you prefer a different voice.
    multilang_voice_id: str = "11labs-Valentina"


settings = Settings()
