"""
Phase 3 — Triage Library & Safety Coaching Engine tests.

Coverage:
  1. validate_tree_safety() rejects unsafe tree (gas_smell → terminal booking without 911)
  2. validate_tree_safety() passes on all 4 seeded system trees
  3. deliver_coaching rejects invalid script_id (Pydantic validation)
  4. deliver_coaching writes to safety_action_ledger (append-only)
  5. deliver_coaching with French language returns French text
  6. Tree activation rejected if safety validation fails
  7. Prompt injection includes triage section when flag ON, excludes when OFF
  8. On-call SMS fires on urgent coaching delivery
  9. Triage node with urgency_level="emergency_911" and is_terminal=True is valid
 10. Cross-tenant: activating tree for tenant A does not affect tenant B
 11. [Adversarial] seeded trees safety property test — all paths safe
 12. [Adversarial] LLM is never called during coaching delivery
 13. [Adversarial] French caller gets French coaching text
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Register all Phase 3 models before Base.metadata is used
import app.models.triage_tree      # noqa: F401
import app.models.triage_node      # noqa: F401
import app.models.coaching_script  # noqa: F401
import app.models.safety_action_ledger  # noqa: F401
import app.models.on_call_rotation # noqa: F401

from app.models.coaching_script import CoachingScript
from app.models.safety_action_ledger import SafetyActionLedger
from app.models.triage_node import TriageNode
from app.models.triage_tree import TriageTree
from app.services.triage_library import TriageLibraryService
from app.services.triage_seed import seed_triage_data
from app.tools.deliver_coaching import DeliverCoachingInput, deliver_coaching


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree(db, trade: str = "test", version: int = 1, is_active: bool = True) -> TriageTree:
    tree = TriageTree(
        id=uuid.uuid4(),
        tenant_id=None,
        trade=trade,
        version=version,
        author_credential="Test Engineer",
        jurisdiction="ALL",
        is_active=is_active,
        language="en",
    )
    db.add(tree)
    return tree


def _make_node(db, tree_id: uuid.UUID, node_key: str, **kwargs) -> TriageNode:
    defaults = {
        "question_text": f"Question for {node_key}?",
        "urgency_level": "standard",
        "next_node_key_map": {},
        "is_terminal": False,
    }
    defaults.update(kwargs)
    node = TriageNode(
        id=uuid.uuid4(),
        tree_id=tree_id,
        node_key=node_key,
        **defaults,
    )
    db.add(node)
    return node


async def _make_coaching_script(db: AsyncSession, script_id: str = "water_main_shutoff_en") -> CoachingScript:
    existing = await db.get(CoachingScript, script_id)
    if existing:
        return existing
    script = CoachingScript(
        id=script_id,
        script_text="Turn the valve clockwise to close the main water shutoff.",
        script_text_fr="Tournez le robinet dans le sens des aiguilles d'une montre.",
        trade="plumbing",
        scenario="burst_pipe_shutoff",
        is_active=True,
    )
    db.add(script)
    await db.flush()
    return script


# ===========================================================================
# Test 1: validate_tree_safety() rejects unsafe tree
# ===========================================================================

@pytest.mark.asyncio
async def test_validate_rejects_gas_node_leading_to_terminal_booking(db: AsyncSession):
    """Gas smell node → terminal booking node without emergency_911 must be rejected."""
    tree = _make_tree(db, trade="hvac_unsafe")
    await db.flush()

    # Root asks about gas smell
    _make_node(
        db, tree.id, "root",
        question_text="Do you smell gas or propane?",
        urgency_level="standard",
        next_node_key_map={"yes": "book_appt", "no": "book_appt"},
        is_terminal=False,
    )
    # Terminal booking node — NOT emergency_911
    _make_node(
        db, tree.id, "book_appt",
        question_text="Great, let me book an appointment.",
        urgency_level="standard",
        is_terminal=True,
    )
    await db.flush()

    svc = TriageLibraryService()
    is_safe, violations = await svc.validate_tree_safety(tree.id, db)

    assert not is_safe, "Unsafe tree should fail validation"
    assert len(violations) > 0, "Should have at least one violation path"


# ===========================================================================
# Test 2: validate_tree_safety() passes on all 4 seeded system trees
# ===========================================================================

@pytest.mark.asyncio
async def test_seeded_trees_pass_safety_validation(db: AsyncSession):
    """All system seed trees must pass safety validation."""
    await seed_triage_data(db)
    await db.commit()

    result = await db.execute(
        select(TriageTree).where(TriageTree.tenant_id == None)  # noqa: E711
    )
    trees = result.scalars().all()
    assert len(trees) >= 4, f"Expected at least 4 seeded trees, got {len(trees)}"

    svc = TriageLibraryService()
    for tree in trees:
        is_safe, violations = await svc.validate_tree_safety(tree.id, db)
        assert is_safe, (
            f"Seeded tree trade={tree.trade} v{tree.version} failed safety: {violations}"
        )


# ===========================================================================
# Test 3: deliver_coaching rejects invalid script_id
# ===========================================================================

def test_deliver_coaching_rejects_invalid_script_id():
    """Pydantic Literal must reject any script_id not in the allowlist."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DeliverCoachingInput(script_id="malicious_freeform_text", language="en")


def test_deliver_coaching_rejects_empty_script_id():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DeliverCoachingInput(script_id="", language="en")


def test_deliver_coaching_rejects_extra_fields():
    """extra='forbid' must reject unknown fields."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DeliverCoachingInput(
            script_id="water_main_shutoff_en",
            language="en",
            injected_field="pwned",  # should be rejected
        )


# ===========================================================================
# Test 4: deliver_coaching writes to safety_action_ledger
# ===========================================================================

@pytest.mark.asyncio
async def test_deliver_coaching_writes_ledger(db: AsyncSession, contractor, call_session):
    """deliver_coaching must write an append-only row to safety_action_ledger."""
    await _make_coaching_script(db)
    db.add(contractor)
    await db.flush()

    context = {"db": db, "contractor": contractor, "call_session": call_session}
    result = await deliver_coaching(
        {"script_id": "water_main_shutoff_en", "language": "en", "node_key": "burst_pipe"},
        context,
    )

    assert result["success"] is True

    ledger_rows = (await db.execute(select(SafetyActionLedger))).scalars().all()
    assert len(ledger_rows) == 1
    row = ledger_rows[0]
    assert row.script_id == "water_main_shutoff_en"
    assert row.call_id == call_session.retell_call_id
    assert row.tenant_id == contractor.id
    assert row.script_text_delivered != ""


# ===========================================================================
# Test 5: deliver_coaching with French language returns French text
# ===========================================================================

@pytest.mark.asyncio
async def test_french_caller_gets_french_coaching(db: AsyncSession, contractor, call_session):
    """Language='fr' must return script_text_fr, not English."""
    await _make_coaching_script(db)
    db.add(contractor)
    await db.flush()

    context = {"db": db, "contractor": contractor, "call_session": call_session}
    result = await deliver_coaching(
        {"script_id": "water_main_shutoff_en", "language": "fr", "node_key": "burst_pipe"},
        context,
    )

    assert result["success"] is True
    # French text should differ from English
    assert "Tournez" in result["coaching_text"], (
        f"Expected French text, got: {result['coaching_text']}"
    )
    assert "clockwise" not in result["coaching_text"], (
        "English text should not appear when French is requested"
    )

    # Also verify ledger recorded French language
    ledger_row = (await db.execute(select(SafetyActionLedger))).scalar_one()
    assert ledger_row.language == "fr"
    assert "Tournez" in ledger_row.script_text_delivered


# ===========================================================================
# Test 6: Tree activation rejected if safety validation fails
# ===========================================================================

@pytest.mark.asyncio
async def test_activate_tree_rejected_when_unsafe(db: AsyncSession, contractor):
    """activate_tree() must raise ValueError for trees that fail safety validation."""
    db.add(contractor)
    tree = _make_tree(db, trade="unsafe_trade")
    await db.flush()

    # Gas mention node → standard terminal (no 911)
    _make_node(
        db, tree.id, "root",
        question_text="Do you smell gas or CO?",
        urgency_level="standard",
        next_node_key_map={"any": "terminal"},
    )
    _make_node(
        db, tree.id, "terminal",
        question_text="Let me book you in.",
        urgency_level="standard",
        is_terminal=True,
    )
    await db.flush()

    svc = TriageLibraryService()
    with pytest.raises(ValueError, match="safety validation"):
        await svc.activate_tree(tree.id, contractor.id, db)


# ===========================================================================
# Test 7: Prompt injection includes triage section when flag ON, excludes when OFF
# ===========================================================================

@pytest.mark.asyncio
async def test_prompt_injection_flag_off(contractor):
    """Flag OFF → build_system_prompt_async returns same as sync version."""
    with patch("app.prompts.builder.settings") as mock_settings:
        mock_settings.triage_library_v2 = False

        from app.prompts.builder import build_system_prompt, build_system_prompt_async
        sync_result = build_system_prompt(contractor)
        async_result = await build_system_prompt_async(contractor, db=None)

        assert sync_result == async_result


@pytest.mark.asyncio
async def test_prompt_injection_flag_on_with_active_tree(db: AsyncSession, contractor):
    """Flag ON with active tree → prompt contains TRIAGE_INSTRUCTIONS."""
    db.add(contractor)
    await db.flush()

    await seed_triage_data(db)
    await db.flush()

    # Set primary_trade so builder picks the right tree
    contractor.trades = ["plumbing"]

    with patch("app.prompts.builder.settings") as mock_settings:
        mock_settings.triage_library_v2 = True

        from app.prompts.builder import build_system_prompt_async
        prompt = await build_system_prompt_async(contractor, db=db)

    assert "TRIAGE_INSTRUCTIONS" in prompt, "Triage section should be injected when flag is ON"
    assert "plumbing" in prompt.lower()


@pytest.mark.asyncio
async def test_prompt_injection_flag_on_no_tree(db: AsyncSession, contractor):
    """Flag ON but no active tree → no TRIAGE_INSTRUCTIONS section (graceful)."""
    db.add(contractor)
    await db.flush()
    contractor.trades = ["nonexistent_trade"]

    with patch("app.prompts.builder.settings") as mock_settings:
        mock_settings.triage_library_v2 = True

        from app.prompts.builder import build_system_prompt_async
        prompt = await build_system_prompt_async(contractor, db=db)

    assert "TRIAGE_INSTRUCTIONS" not in prompt


# ===========================================================================
# Test 8: On-call SMS fires on urgent coaching delivery
# ===========================================================================

@pytest.mark.asyncio
async def test_oncall_sms_fires_on_urgent_delivery(db: AsyncSession, contractor, call_session):
    """Urgent urgency_level on the node should trigger an on-call SMS via OutboundGateway."""
    db.add(contractor)
    await db.flush()

    # Add a triage node with urgency=urgent to influence the ledger
    tree = _make_tree(db, trade="plumbing")
    await db.flush()
    _make_node(
        db, tree.id, "burst_pipe",
        question_text="Burst pipe?",
        urgency_level="urgent",
        coaching_script_id="water_main_shutoff_en",
        is_terminal=False,
    )
    await _make_coaching_script(db)
    await db.flush()

    mock_gateway_result = MagicMock()
    mock_gateway_result.success = True

    with patch("app.tools.deliver_coaching._send_oncall_alert", new=AsyncMock()) as mock_alert:
        context = {"db": db, "contractor": contractor, "call_session": call_session}
        result = await deliver_coaching(
            {"script_id": "water_main_shutoff_en", "language": "en", "node_key": "burst_pipe"},
            context,
        )

    assert result["success"] is True
    # _send_oncall_alert should have been called (urgent node triggers oncall SMS)
    mock_alert.assert_called_once()


# ===========================================================================
# Test 9: emergency_911 terminal node is valid
# ===========================================================================

@pytest.mark.asyncio
async def test_emergency_911_terminal_node_is_valid(db: AsyncSession):
    """A node with urgency_level='emergency_911' and is_terminal=True must pass validation."""
    tree = _make_tree(db, trade="electrical_test")
    await db.flush()

    _make_node(
        db, tree.id, "root",
        question_text="What's the electrical issue?",
        urgency_level="standard",
        next_node_key_map={"spark": "sparking_outlet"},
    )
    _make_node(
        db, tree.id, "sparking_outlet",
        question_text="This is an emergency. Do not touch the outlet. Call 911.",
        urgency_level="emergency_911",
        is_terminal=True,
    )
    await db.flush()

    svc = TriageLibraryService()
    is_safe, violations = await svc.validate_tree_safety(tree.id, db)
    assert is_safe, f"emergency_911 terminal node should be valid. Violations: {violations}"
    assert len(violations) == 0


# ===========================================================================
# Test 10: Cross-tenant isolation — activating tree for tenant A does not affect tenant B
# ===========================================================================

@pytest.mark.asyncio
async def test_cross_tenant_isolation(db: AsyncSession):
    """Activating a tree for tenant A must not activate or deactivate trees for tenant B."""
    tenant_a_id = uuid.uuid4()
    tenant_b_id = uuid.uuid4()

    from app.models.contractor import Contractor

    contractor_a = Contractor(
        id=tenant_a_id,
        name="Contractor A",
        agent_name="Alice",
        phone_number="+15550001111",
        api_key="key-a",
        trades=["plumbing"],
        service_areas=["T2N"],
        timezone="America/Edmonton",
        free_estimate=False,
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        is_active=True,
    )
    contractor_b = Contractor(
        id=tenant_b_id,
        name="Contractor B",
        agent_name="Bob",
        phone_number="+15550002222",
        api_key="key-b",
        trades=["plumbing"],
        service_areas=["T2P"],
        timezone="America/Edmonton",
        free_estimate=False,
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        is_active=True,
    )
    db.add(contractor_a)
    db.add(contractor_b)
    await db.flush()

    # Create a safe tree for tenant A
    tree_a = TriageTree(
        id=uuid.uuid4(),
        tenant_id=tenant_a_id,
        trade="plumbing",
        version=1,
        author_credential="Test",
        jurisdiction="ALL",
        is_active=False,
        language="en",
    )
    db.add(tree_a)

    # Create an already-active tree for tenant B (different trade doesn't matter, but let's use same for isolation test)
    tree_b = TriageTree(
        id=uuid.uuid4(),
        tenant_id=tenant_b_id,
        trade="plumbing",
        version=1,
        author_credential="Test",
        jurisdiction="ALL",
        is_active=True,
        language="en",
    )
    db.add(tree_b)
    await db.flush()

    # Add safe nodes to tree_a (no life-safety keywords, clean terminal)
    _make_node(
        db, tree_a.id, "root",
        question_text="What's the plumbing issue?",
        urgency_level="standard",
        next_node_key_map={"any": "done"},
    )
    _make_node(db, tree_a.id, "done", question_text="Got it.", urgency_level="standard", is_terminal=True)
    await db.flush()

    svc = TriageLibraryService()
    await svc.activate_tree(tree_a.id, tenant_a_id, db)
    await db.flush()

    # Reload tree_b from DB
    await db.refresh(tree_b)
    assert tree_b.is_active is True, "Tenant B's tree must NOT be deactivated by Tenant A's activation"


# ===========================================================================
# Test 11 (Adversarial): seeded trees safety property — enumerate all paths
# ===========================================================================

@pytest.mark.asyncio
async def test_seeded_trees_safety_property(db: AsyncSession):
    """
    Property test: enumerate ALL paths in each seeded tree.
    Any path through a life-safety keyword node must terminate at emergency_911
    or have no subsequent booking terminal.
    """
    await seed_triage_data(db)
    await db.flush()

    svc = TriageLibraryService()
    result = await db.execute(
        select(TriageTree).where(TriageTree.tenant_id == None)  # noqa: E711
    )
    trees = result.scalars().all()

    assert len(trees) >= 4, "All 4 system trees must be seeded"

    for tree in trees:
        is_safe, violations = await svc.validate_tree_safety(tree.id, db)
        assert is_safe, (
            f"SAFETY PROPERTY VIOLATION — trade={tree.trade} v{tree.version}:\n"
            + "\n".join(violations)
        )


# ===========================================================================
# Test 12 (Adversarial): LLM is NEVER called during coaching delivery
# ===========================================================================

@pytest.mark.asyncio
async def test_coaching_text_is_not_llm_generated(db: AsyncSession, contractor, call_session):
    """
    Mock the Anthropic client and assert it is NEVER called during deliver_coaching.
    Coaching text must ALWAYS come from the DB, never from LLM generation.
    """
    db.add(contractor)
    await _make_coaching_script(db)
    await db.flush()

    with patch("anthropic.AsyncAnthropic") as MockAnthropic:
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client

        context = {"db": db, "contractor": contractor, "call_session": call_session}
        result = await deliver_coaching(
            {"script_id": "water_main_shutoff_en", "language": "en", "node_key": "burst_pipe"},
            context,
        )

    assert result["success"] is True
    # The Anthropic constructor must never have been called
    MockAnthropic.assert_not_called()
    # And the create method must never have been called on any instance
    mock_client.messages.create.assert_not_called()


# ===========================================================================
# Test 13 (Adversarial): French coaching returns French text before English
# ===========================================================================

@pytest.mark.asyncio
async def test_fr_language_prioritizes_fr_text(db: AsyncSession, contractor, call_session):
    """French language request must return script_text_fr, not script_text (English)."""
    db.add(contractor)
    await _make_coaching_script(db)  # has both en and fr text
    await db.flush()

    context = {"db": db, "contractor": contractor, "call_session": call_session}

    # English delivery
    en_result = await deliver_coaching(
        {"script_id": "water_main_shutoff_en", "language": "en", "node_key": "burst_pipe"},
        context,
    )
    # French delivery
    fr_result = await deliver_coaching(
        {"script_id": "water_main_shutoff_en", "language": "fr", "node_key": "burst_pipe"},
        context,
    )

    assert en_result["coaching_text"] != fr_result["coaching_text"], (
        "English and French texts must differ"
    )
    assert "clockwise" in en_result["coaching_text"].lower(), "English text should contain English content"
    assert "Tournez" in fr_result["coaching_text"], "French text should be the French variant"
