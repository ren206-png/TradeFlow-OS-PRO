from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intake_template import IntakeTemplate


class IntakeService:
    async def get_template(
        self, trade: str, contractor_id: int, db: AsyncSession
    ) -> IntakeTemplate | None:
        """Get contractor-specific template first, fall back to system template."""
        # Try contractor-specific first
        result = await db.execute(
            select(IntakeTemplate).where(
                IntakeTemplate.contractor_id == contractor_id,
                IntakeTemplate.trade == trade,
            )
        )
        t = result.scalar_one_or_none()
        if t:
            return t
        # Fall back to system template
        result = await db.execute(
            select(IntakeTemplate).where(
                IntakeTemplate.is_system == True,  # noqa: E712
                IntakeTemplate.trade == trade,
            )
        )
        return result.scalar_one_or_none()

    async def format_questions_for_prompt(self, template: IntakeTemplate) -> str:
        """Format questions as a numbered list for system prompt injection."""
        if not template or not template.questions:
            return ""
        lines = ["INTAKE QUESTIONS — ask these in order, one at a time:"]
        for i, q in enumerate(template.questions, 1):
            req = " (required)" if q.get("required") else ""
            lines.append(f"{i}. {q['text']}{req}")
        return "\n".join(lines)


async def seed_system_templates(db: AsyncSession) -> None:
    """Insert system templates for core trades if they don't already exist."""
    templates = [
        {
            "trade": "plumbing",
            "questions": [
                {"id": "q1", "text": "Is it hot water, cold water, or both?", "required": True, "type": "open"},
                {"id": "q2", "text": "Is water currently leaking or shut off?", "required": True, "type": "open"},
                {"id": "q3", "text": "What's the property type — house, condo, or commercial?", "required": False, "type": "open"},
            ],
        },
        {
            "trade": "hvac",
            "questions": [
                {"id": "q1", "text": "Is the system not heating, not cooling, or completely off?", "required": True, "type": "open"},
                {"id": "q2", "text": "How old is the unit approximately?", "required": False, "type": "open"},
                {"id": "q3", "text": "Any unusual sounds or smells?", "required": False, "type": "open"},
            ],
        },
        {
            "trade": "electrical",
            "questions": [
                {"id": "q1", "text": "Is this a complete outage or partial?", "required": True, "type": "open"},
                {"id": "q2", "text": "Have you checked the breaker panel?", "required": True, "type": "open"},
                {"id": "q3", "text": "Any burning smell or visible sparking?", "required": True, "type": "open"},
            ],
        },
        {
            "trade": "roofing",
            "questions": [
                {"id": "q1", "text": "Is there active water coming in right now?", "required": True, "type": "open"},
                {"id": "q2", "text": "How old is the roof approximately?", "required": False, "type": "open"},
                {"id": "q3", "text": "What's the roof material — shingles, metal, flat?", "required": False, "type": "open"},
            ],
        },
        {
            "trade": "general",
            "questions": [
                {"id": "q1", "text": "Can you describe the main issue you're experiencing?", "required": True, "type": "open"},
                {"id": "q2", "text": "Is this affecting your ability to use the space normally?", "required": False, "type": "open"},
                {"id": "q3", "text": "Has this happened before?", "required": False, "type": "open"},
            ],
        },
    ]

    for tmpl_data in templates:
        # Check if system template for this trade already exists
        result = await db.execute(
            select(IntakeTemplate).where(
                IntakeTemplate.is_system == True,  # noqa: E712
                IntakeTemplate.trade == tmpl_data["trade"],
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            db.add(
                IntakeTemplate(
                    contractor_id=None,
                    trade=tmpl_data["trade"],
                    is_system=True,
                    questions=tmpl_data["questions"],
                )
            )

    await db.flush()
