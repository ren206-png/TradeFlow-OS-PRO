"""
Root-level conftest: loads .env.test before any app modules are imported,
so that pydantic Settings can find all required fields.
"""
import os
from pathlib import Path

# Load .env.test into the environment before anything else runs.
env_test = Path(__file__).parent / ".env.test"
if env_test.exists():
    for line in env_test.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
