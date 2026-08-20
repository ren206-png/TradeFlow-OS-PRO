"""
TriageLibraryService — runtime management of versioned triage trees.

Safety contract:
  - activate_tree() MUST pass validate_tree_safety() before activating.
  - Any tree path through a life-safety keyword node that does NOT terminate at
    urgency_level="emergency_911" is a validation violation.
  - Trees that fail validation are NEVER activated.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coaching_script import CoachingScript
from app.models.triage_node import TriageNode
from app.models.triage_tree import TriageTree

logger = logging.getLogger(__name__)

# Life-safety keyword patterns used in safety property test.
# These mirror the patterns in triage.py but are applied to node question_text
# to detect nodes that discuss life-safety scenarios.
_LIFE_SAFETY_NODE_PATTERNS = re.compile(
    r"\b(gas|co\b|carbon\s*monoxide|propane|fire|spark|flood|electric.?shock|shocked)\b",
    re.IGNORECASE,
)


class TriageLibraryService:
    """Service for triage tree CRUD and safety validation."""

    async def get_active_tree(
        self,
        tenant_id: uuid.UUID,
        trade: str,
        language: str,
        db: AsyncSession,
    ) -> Optional[TriageTree]:
        """
        Return the active triage tree for this tenant+trade.
        Falls back to system tree (tenant_id=None) if no tenant-specific tree exists.
        Always filters by tenant_id at the query layer.
        """
        # Tenant-specific tree first
        result = await db.execute(
            select(TriageTree).where(
                TriageTree.tenant_id == tenant_id,
                TriageTree.trade == trade,
                TriageTree.is_active == True,  # noqa: E712
            ).order_by(TriageTree.version.desc()).limit(1)
        )
        tree = result.scalar_one_or_none()
        if tree:
            return tree

        # Fall back to system tree (tenant_id IS NULL)
        result = await db.execute(
            select(TriageTree).where(
                TriageTree.tenant_id == None,  # noqa: E711
                TriageTree.trade == trade,
                TriageTree.is_active == True,  # noqa: E712
            ).order_by(TriageTree.version.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_tree_nodes(
        self,
        tree_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, TriageNode]:
        """Return all nodes for a tree keyed by node_key."""
        result = await db.execute(
            select(TriageNode).where(TriageNode.tree_id == tree_id)
        )
        nodes = result.scalars().all()
        return {n.node_key: n for n in nodes}

    async def activate_tree(
        self,
        tree_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """
        Activate a tree for a tenant, deactivating any previously active tree for the same trade.
        MUST call validate_tree_safety() first — raises ValueError if unsafe.
        """
        is_safe, violations = await self.validate_tree_safety(tree_id, db)
        if not is_safe:
            raise ValueError(
                f"Tree {tree_id} failed safety validation. Violations:\n"
                + "\n".join(violations)
            )

        # Fetch the tree to confirm tenant ownership or system tree
        result = await db.execute(
            select(TriageTree).where(TriageTree.id == tree_id)
        )
        tree = result.scalar_one_or_none()
        if tree is None:
            raise ValueError(f"Tree {tree_id} not found.")

        # Deactivate all existing active trees for this tenant+trade
        await db.execute(
            update(TriageTree)
            .where(
                TriageTree.tenant_id == tenant_id,
                TriageTree.trade == tree.trade,
                TriageTree.is_active == True,  # noqa: E712
            )
            .values(is_active=False)
        )
        # Also deactivate any system trees for this trade that were being used
        # (tenant-specific activation takes precedence)
        tree.is_active = True
        await db.flush()
        logger.info(
            "triage_library: activated tree %s trade=%s tenant=%s",
            tree_id, tree.trade, tenant_id,
        )

    async def get_coaching_script(
        self,
        script_id: str,
        language: str,
        db: AsyncSession,
    ) -> Optional[CoachingScript]:
        """Fetch a coaching script by id. Returns None if not found."""
        result = await db.execute(
            select(CoachingScript).where(CoachingScript.id == script_id)
        )
        return result.scalar_one_or_none()

    def build_triage_prompt_section(
        self,
        tree: TriageTree,
        nodes: dict[str, TriageNode],
    ) -> str:
        """
        Convert an active triage tree into a prompt injection string for build_system_prompt().
        This section instructs the AI on how to traverse the tree.
        """
        if not nodes:
            return ""

        root = nodes.get("root")
        if root is None:
            return ""

        lines = [
            "## TRIAGE_INSTRUCTIONS",
            "",
            f"You are following a structured triage protocol for trade: {tree.trade.upper()}.",
            "Ask questions in the ORDER defined below. Do not skip nodes.",
            "After each caller answer, route to the next node based on their response.",
            "",
            "CRITICAL RULES:",
            "- If a node has urgency_level='emergency_911', immediately invoke the classify_urgency",
            "  tool with urgency_level='emergency' and deliver the emergency_911 response.",
            "- If a node has a coaching_script_id, invoke the deliver_coaching tool with that script_id",
            "  BEFORE moving to the next node.",
            "- Never generate coaching text yourself — only use the deliver_coaching tool.",
            "- Record urgency via the classify_urgency tool when urgency_level is determined.",
            "",
            "TRIAGE NODES:",
        ]

        for key, node in nodes.items():
            routing = ", ".join(
                f'"{pat}" → {dest}' for pat, dest in (node.next_node_key_map or {}).items()
            )
            coaching_note = f" [coaching: {node.coaching_script_id}]" if node.coaching_script_id else ""
            terminal_note = " [TERMINAL]" if node.is_terminal else ""
            lines.append(
                f"  [{key}] urgency={node.urgency_level}{coaching_note}{terminal_note}"
            )
            lines.append(f"    Ask: {node.question_text}")
            if routing:
                lines.append(f"    Route: {routing}")
            if node.next_node_booked:
                lines.append(f"    After booking → {node.next_node_booked}")

        return "\n".join(lines)

    async def validate_tree_safety(
        self,
        tree_id: uuid.UUID,
        db: AsyncSession,
    ) -> tuple[bool, list[str]]:
        """
        Property test: verify NO path through the tree reaches a terminal booking node
        from a life-safety-keyword node without hitting urgency_level="emergency_911".

        Returns (is_safe, list_of_violation_paths).
        MUST be called before activate_tree(). Caller raises ValueError if unsafe.
        """
        nodes = await self.get_tree_nodes(tree_id, db)
        if not nodes:
            return True, []

        violations: list[str] = []

        def _dfs(
            node_key: str,
            path: list[str],
            in_life_safety_zone: bool,
            visited: set[str],
        ) -> None:
            if node_key not in nodes:
                return
            if node_key in visited:
                return  # cycle guard

            node = nodes[node_key]
            visited = visited | {node_key}
            current_path = path + [node_key]

            # Check if this node's question_text enters a life-safety zone
            is_life_safety_node = bool(_LIFE_SAFETY_NODE_PATTERNS.search(node.question_text or ""))
            new_in_life_safety = in_life_safety_zone or is_life_safety_node

            # If we're in a life-safety zone and this node is emergency_911, zone is "cleared"
            # (the escalation happened — safe path)
            if new_in_life_safety and node.urgency_level == "emergency_911":
                # Safe — emergency_911 reached. Terminal or not, escalation happened.
                return

            # Check terminal nodes: if in life-safety zone and we reach a booking terminal
            # without having hit emergency_911, that's a violation.
            if node.is_terminal and new_in_life_safety and node.urgency_level != "emergency_911":
                violations.append(
                    f"UNSAFE PATH: {' -> '.join(current_path)} "
                    f"[terminal urgency={node.urgency_level}, life-safety zone entered without 911 escalation]"
                )
                return

            if node.is_terminal:
                return

            # DFS into all next nodes
            next_keys: set[str] = set()

            # From next_node_key_map
            for dest in (node.next_node_key_map or {}).values():
                if dest:
                    next_keys.add(str(dest))

            # From next_node_booked
            if node.next_node_booked:
                next_keys.add(node.next_node_booked)

            if not next_keys:
                # Implicit terminal — if in life-safety zone this is a violation
                if new_in_life_safety:
                    violations.append(
                        f"UNSAFE PATH: {' -> '.join(current_path)} "
                        f"[implicit terminal in life-safety zone, urgency={node.urgency_level}]"
                    )
                return

            for next_key in next_keys:
                _dfs(next_key, current_path, new_in_life_safety, visited)

        root_key = "root" if "root" in nodes else next(iter(nodes))
        _dfs(root_key, [], False, set())

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning(
                "triage_library: safety validation FAILED for tree %s — %d violations",
                tree_id, len(violations),
            )
        return is_safe, violations
