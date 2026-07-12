import re
from enum import Enum


class UrgencyLevel(str, Enum):
    LIFE_SAFETY = "life_safety"
    EMERGENCY = "emergency"
    URGENT = "urgent"
    STANDARD = "standard"


# Hardcoded life-safety patterns — cannot be disabled by any flag or tenant setting
_LIFE_SAFETY_PATTERNS = [
    r"\bgas\s*(smell|leak|line)\b",
    r"\bsmell(ing)?\s*(gas|propane)\b",
    r"\bcarbon\s*monoxide\b",
    r"\bco\s*(detector|alarm|leak)\b",
    r"\bsparking?\b",
    r"\belectrical\s*fire\b",
    r"\bhouse\s*(is\s*)?(on\s*)?fire\b",
    r"\bsewer\s*(backup|overflow)\b.*flood",
    r"\bflood(ing)?\s*(basement|house|entire)\b",
    r"\bno\s*power\s*(to\s*)?(the\s*)?(whole|entire)\s*(house|building)\b",
    r"\belectric(al)?\s*shock\b",
    r"\bgot\s*shocked\b",
]

_LIFE_SAFETY_REGEX = re.compile(
    "|".join(_LIFE_SAFETY_PATTERNS),
    re.IGNORECASE
)

LIFE_SAFETY_RESPONSE = (
    "I need to stop you right there — what you're describing sounds like it could be a life-safety emergency. "
    "Please hang up and call 911 immediately, or evacuate the building if there's any risk to your safety. "
    "Once you're safe, please call us back and we'll have someone there as soon as possible. "
    "Please call 911 now."
)


def classify_life_safety(text: str) -> bool:
    """Returns True if the text matches any life-safety pattern.
    This function is HARDCODED and cannot be disabled by any flag or tenant config.
    """
    return bool(_LIFE_SAFETY_REGEX.search(text))


# Urgency taxonomy for the classify_urgency Claude tool
URGENCY_LEVELS = {
    "emergency": "Active failure causing damage right now (burst pipe flooding, total heat loss in winter, no power)",
    "urgent": "Same-day issue needed (no hot water, toilet not flushing, AC out in summer heat)",
    "standard": "Scheduled service (maintenance, quote, non-critical repair)",
}


def get_urgency_tool_schema() -> dict:
    """Returns the Claude tool schema for classify_urgency."""
    return {
        "name": "classify_urgency",
        "description": "Classify the urgency level of the caller's issue based on what they described.",
        "input_schema": {
            "type": "object",
            "properties": {
                "urgency_level": {
                    "type": "string",
                    "enum": ["emergency", "urgent", "standard"],
                    "description": "The urgency level: emergency (active damage now), urgent (same-day needed), standard (scheduled)"
                },
                "reason": {
                    "type": "string",
                    "description": "One-sentence explanation of why this urgency level was chosen"
                }
            },
            "required": ["urgency_level", "reason"]
        }
    }
