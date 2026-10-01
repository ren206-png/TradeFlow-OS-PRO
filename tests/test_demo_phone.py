"""Demo phone config: validation, formatting, landing render, and no stray number literals."""
from __future__ import annotations

import pathlib
import re

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.utils.phone import format_display, is_nanp_e164, tel_href

ROOT = pathlib.Path(__file__).resolve().parents[1]
_BASE = dict(anthropic_api_key="x", retell_api_key="x", database_url="sqlite+aiosqlite://", secret_key="x")


@pytest.mark.parametrize("value", ["+15878001544", "+17756183748", "", "  +15878001544  "])
def test_demo_phone_accepts_e164_or_empty(value):
    assert Settings(**_BASE, demo_phone_number=value).demo_phone_number == value.strip()


@pytest.mark.parametrize("value", ["5878001544", "(587) 800-1544", "+1587800154", "+445878001544", "+1 587 800 1544"])
def test_demo_phone_rejects_non_e164(value):
    with pytest.raises(ValidationError):
        Settings(**_BASE, demo_phone_number=value)


def test_formatters():
    assert format_display("+15878001544") == "(587) 800-1544"
    assert format_display("+17756183748") == "(775) 618-3748"
    assert format_display("") == ""
    assert tel_href("+15878001544") == "tel:+15878001544"
    assert tel_href("") == ""
    assert is_nanp_e164("+15878001544") and not is_nanp_e164("15878001544")


def test_landing_renders_configured_number(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "demo_phone_number", "+15878001544")
    html = TestClient(app).get("/").text
    assert 'href="tel:+15878001544"' in html
    assert "(587) 800-1544" in html
    assert "+17756183748" not in html


# Fictional/example numbers that are allowed to appear in source as documentation or placeholders.
_ALLOWED_LITERALS = {"+10000000000", "+12345678901", "+12125551234"}
_PHONE = re.compile(r"\+1\d{10}|(?<![\d$])\(?\b[2-9]\d{2}\)?[-. ][2-9]\d{2}[-. ]\d{4}\b")


def _is_fictional(match: str) -> bool:
    digits = re.sub(r"\D", "", match)[-10:]
    return "555" in (digits[:3], digits[3:6])  # 555 area code or exchange: fictional


def test_no_real_phone_literals_outside_config_and_tests():
    offenders = []
    for path in list((ROOT / "app").rglob("*")) + list((ROOT / "scripts").rglob("*")):
        if path.suffix not in {".py", ".html", ".js", ".json", ".yml", ".yaml", ".txt", ".md"}:
            continue
        for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            for m in _PHONE.finditer(line):
                lit = m.group(0)
                if lit in _ALLOWED_LITERALS or _is_fictional(lit):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {lit}")
    assert not offenders, "Real phone numbers must come from config/env:\n" + "\n".join(offenders)


def test_demo_call_started_at_is_timezone_aware():
    """check_demo_daily_cap compares against an aware datetime; the column must be TIMESTAMPTZ."""
    from app.models.demo_call import DemoCall
    assert DemoCall.__table__.c.started_at.type.timezone is True
