"""
Triage Library Admin API — Phase 3.

Admin-only routes (X-Admin-Key auth) for managing triage trees,
coaching scripts, and running safety validation.

All routes require X-Admin-Key header matching settings.admin_password.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.coaching_script import CoachingScript
from app.models.triage_node import TriageNode
from app.models.triage_tree import TriageTree
from app.services.triage_library import TriageLibraryService

router = APIRouter(prefix="/admin/triage", tags=["admin", "triage"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_admin(x_admin_key: str = Header(...)) -> None:
    expected = settings.admin_password or settings.secret_key
    if not expected or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key.")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TriageTreeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    trade: str
    version: int
    author_credential: str
    jurisdiction: str
    is_active: bool
    language: str


class TriageNodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    node_key: str
    question_text: str
    question_text_fr: Optional[str]
    urgency_level: str
    urgency_escalation_trigger: Optional[str]
    coaching_script_id: Optional[str]
    next_node_booked: Optional[str]
    next_node_key_map: Any
    is_terminal: bool


class CoachingScriptOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    trade: str
    scenario: str
    script_text: str
    script_text_fr: Optional[str]
    is_active: bool


class SafetyCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tree_id: str
    is_safe: bool
    violations: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/trees", response_model=list[TriageTreeOut])
async def list_trees(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> list[TriageTreeOut]:
    """List all system trees (tenant_id IS NULL)."""
    result = await db.execute(
        select(TriageTree).where(TriageTree.tenant_id == None).order_by(  # noqa: E711
            TriageTree.trade, TriageTree.version.desc()
        )
    )
    trees = result.scalars().all()
    return [
        TriageTreeOut(
            id=t.id,
            tenant_id=t.tenant_id,
            trade=t.trade,
            version=t.version,
            author_credential=t.author_credential,
            jurisdiction=t.jurisdiction,
            is_active=t.is_active,
            language=t.language,
        )
        for t in trees
    ]


@router.get("/trees/{tree_id}/nodes", response_model=list[TriageNodeOut])
async def list_tree_nodes(
    tree_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> list[TriageNodeOut]:
    """List all nodes for a given tree."""
    result = await db.execute(
        select(TriageNode).where(TriageNode.tree_id == tree_id)
    )
    nodes = result.scalars().all()
    if not nodes:
        raise HTTPException(status_code=404, detail="Tree not found or has no nodes.")
    return [
        TriageNodeOut(
            id=n.id,
            tree_id=n.tree_id,
            node_key=n.node_key,
            question_text=n.question_text,
            question_text_fr=n.question_text_fr,
            urgency_level=n.urgency_level,
            urgency_escalation_trigger=n.urgency_escalation_trigger,
            coaching_script_id=n.coaching_script_id,
            next_node_booked=n.next_node_booked,
            next_node_key_map=n.next_node_key_map,
            is_terminal=n.is_terminal,
        )
        for n in nodes
    ]


@router.post("/trees/{tree_id}/activate/{tenant_id}", status_code=200)
async def activate_tree_for_tenant(
    tree_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> dict:
    """
    Activate a triage tree for a tenant.
    Runs safety validation first — rejects if any unsafe path exists.
    """
    svc = TriageLibraryService()
    try:
        await svc.activate_tree(tree_id, tenant_id, db)
        await db.commit()
        return {"success": True, "tree_id": str(tree_id), "tenant_id": str(tenant_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("triage_admin: activate_tree failed | tree=%s tenant=%s err=%s", tree_id, tenant_id, exc)
        raise HTTPException(status_code=500, detail="Activation failed.")


@router.get("/safety-check/{tree_id}", response_model=SafetyCheckOut)
async def safety_check(
    tree_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> SafetyCheckOut:
    """Run safety validation on a tree without activating it."""
    svc = TriageLibraryService()
    is_safe, violations = await svc.validate_tree_safety(tree_id, db)
    return SafetyCheckOut(
        tree_id=str(tree_id),
        is_safe=is_safe,
        violations=violations,
    )


@router.get("/scripts", response_model=list[CoachingScriptOut])
async def list_scripts(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> list[CoachingScriptOut]:
    """List all coaching scripts."""
    result = await db.execute(select(CoachingScript).order_by(CoachingScript.id))
    scripts = result.scalars().all()
    return [
        CoachingScriptOut(
            id=s.id,
            trade=s.trade,
            scenario=s.scenario,
            script_text=s.script_text,
            script_text_fr=s.script_text_fr,
            is_active=s.is_active,
        )
        for s in scripts
    ]
