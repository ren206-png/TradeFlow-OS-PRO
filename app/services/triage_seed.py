"""
Triage seed data — system-level trees and coaching scripts.
tenant_id=None → available to all tenants as fallback.
All seed operations are idempotent.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coaching_script import CoachingScript
from app.models.triage_node import TriageNode
from app.models.triage_tree import TriageTree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coaching scripts seed data
# ---------------------------------------------------------------------------

_COACHING_SCRIPTS: list[dict] = [
    {
        "id": "water_main_shutoff_en",
        "trade": "plumbing",
        "scenario": "burst_pipe_shutoff",
        "script_text": (
            "Your main water shutoff is usually near the front of your home where the water line "
            "enters — often in the basement, crawl space, or utility room. Turn it clockwise to "
            "close. This will stop water flow to the whole house."
        ),
        "script_text_fr": (
            "Votre robinet d'arrêt principal se trouve généralement près de l'entrée de votre "
            "maison, là où la conduite d'eau entre — souvent au sous-sol, dans le vide sanitaire "
            "ou dans la buanderie. Tournez-le dans le sens des aiguilles d'une montre pour le "
            "fermer. Cela coupera l'eau dans toute la maison."
        ),
        "is_active": True,
    },
    {
        "id": "breaker_safety_en",
        "trade": "electrical",
        "scenario": "tripping_breaker_safety",
        "script_text": (
            "Do not keep resetting the breaker repeatedly — this can cause overheating. "
            "If it trips again, leave the breaker in the off position and we'll diagnose "
            "the cause when we arrive."
        ),
        "script_text_fr": (
            "Ne réenclenchez pas le disjoncteur de manière répétée — cela peut provoquer "
            "une surchauffe. S'il se déclenche à nouveau, laissez-le en position éteinte "
            "et nous diagnostiquerons la cause à notre arrivée."
        ),
        "is_active": True,
    },
    {
        "id": "thermostat_off_en",
        "trade": "hvac",
        "scenario": "equipment_cycling_shutoff",
        "script_text": (
            "Set your thermostat to OFF, not just higher or lower. This will stop the equipment "
            "from cycling and prevent further damage until we can get there."
        ),
        "script_text_fr": (
            "Réglez votre thermostat sur ARRÊT, pas seulement plus haut ou plus bas. Cela "
            "arrêtera le fonctionnement de l'équipement et évitera d'autres dommages jusqu'à "
            "notre arrivée."
        ),
        "is_active": True,
    },
]

# ---------------------------------------------------------------------------
# Triage trees seed data
# ---------------------------------------------------------------------------

_TRIAGE_TREES: list[dict] = [
    # ------------------------------------------------------------------
    # Plumbing tree
    # ------------------------------------------------------------------
    {
        "trade": "plumbing",
        "version": 1,
        "author_credential": "Red Seal Plumber, 15yr",
        "jurisdiction": "ALL",
        "is_active": True,
        "language": "en",
        "nodes": [
            {
                "node_key": "root",
                "question_text": "What's the plumbing issue today?",
                "urgency_level": "standard",
                "next_node_key_map": {
                    "burst": "burst_pipe",
                    "pipe": "burst_pipe",
                    "leak": "burst_pipe",
                    "sewer": "sewer_backup",
                    "backup": "sewer_backup",
                    "hot water": "no_hot_water",
                    "frozen": "frozen_lines",
                    "other": "other_plumbing",
                },
                "is_terminal": False,
            },
            {
                "node_key": "burst_pipe",
                "question_text": (
                    "Is there water actively spraying or flooding? "
                    "I can guide you to the main shutoff right now."
                ),
                "urgency_level": "urgent",
                "coaching_script_id": "water_main_shutoff_en",
                "next_node_key_map": {
                    "yes": "other_plumbing",
                    "no": "other_plumbing",
                },
                "is_terminal": False,
            },
            {
                "node_key": "sewer_backup",
                "question_text": (
                    "Is sewage backing up into fixtures or is water flooding the house?"
                ),
                "urgency_level": "urgent",
                "urgency_escalation_trigger": r"flood(ing)?\s*(house|entire|basement)",
                "next_node_key_map": {
                    "flooding house": "sewer_flood_911",
                    "flooding basement": "sewer_flood_911",
                    "flooding entire": "sewer_flood_911",
                    "backup only": "other_plumbing",
                    "no flooding": "other_plumbing",
                },
                "is_terminal": False,
            },
            {
                "node_key": "sewer_flood_911",
                "question_text": (
                    "This sounds like a flooding emergency. If water is rising rapidly, "
                    "please evacuate to safety and call 911. We are dispatching emergency help now."
                ),
                "urgency_level": "emergency_911",
                "is_terminal": True,
            },
            {
                "node_key": "no_hot_water",
                "question_text": (
                    "How long have you been without hot water, and do you have an electric or tank water heater?"
                ),
                "urgency_level": "standard",
                "next_node_key_map": {
                    "yes": "other_plumbing",
                    "no": "other_plumbing",
                },
                "is_terminal": False,
            },
            {
                "node_key": "frozen_lines",
                "question_text": (
                    "Are pipes frozen with no flow, or do you see visible damage? "
                    "Do not use open flame to thaw pipes."
                ),
                "urgency_level": "urgent",
                "next_node_key_map": {
                    "yes": "other_plumbing",
                    "no": "other_plumbing",
                },
                "is_terminal": False,
            },
            {
                "node_key": "other_plumbing",
                "question_text": (
                    "Can you describe the issue in a bit more detail so I can get the right tech to you?"
                ),
                "urgency_level": "standard",
                "is_terminal": True,
            },
        ],
    },
    # ------------------------------------------------------------------
    # HVAC tree
    # ------------------------------------------------------------------
    {
        "trade": "hvac",
        "version": 1,
        "author_credential": "HVAC Red Seal, 12yr",
        "jurisdiction": "ALL",
        "is_active": True,
        "language": "en",
        "nodes": [
            {
                "node_key": "root",
                "question_text": "What's the heating or cooling issue today?",
                "urgency_level": "standard",
                "next_node_key_map": {
                    "gas": "gas_smell",
                    "smell": "gas_smell",
                    "no heat": "no_heat",
                    "heat": "no_heat",
                    "no cool": "no_cool",
                    "cool": "no_cool",
                    "refrigerant": "refrigerant_leak",
                    "short cycl": "short_cycling",
                    "other": "other_hvac",
                },
                "is_terminal": False,
            },
            {
                "node_key": "gas_smell",
                "question_text": (
                    "I'm sending emergency services immediately. "
                    "Please leave the building now and call 911."
                ),
                "urgency_level": "emergency_911",
                "is_terminal": True,
            },
            {
                "node_key": "no_heat",
                "question_text": (
                    "How long have you been without heat, and what is the outdoor temperature? "
                    "Is anyone in the home vulnerable — elderly, young children, or ill?"
                ),
                "urgency_level": "urgent",
                "coaching_script_id": "thermostat_off_en",
                "next_node_key_map": {
                    "yes": "other_hvac",
                    "no": "other_hvac",
                },
                "is_terminal": False,
            },
            {
                "node_key": "no_cool",
                "question_text": (
                    "How long has the AC been out? Is there a heat advisory or extreme temperature warning in your area?"
                ),
                "urgency_level": "standard",
                "next_node_key_map": {
                    "heat warning": "other_hvac",
                    "extreme": "other_hvac",
                    "no": "other_hvac",
                },
                "is_terminal": False,
            },
            {
                "node_key": "refrigerant_leak",
                "question_text": (
                    "Do you see ice on the lines, hear hissing, or notice an oily residue? "
                    "Do not run the system — this can damage the compressor."
                ),
                "urgency_level": "urgent",
                "next_node_key_map": {
                    "yes": "other_hvac",
                    "no": "other_hvac",
                },
                "is_terminal": False,
            },
            {
                "node_key": "short_cycling",
                "question_text": (
                    "Is the system turning on and off rapidly — every few minutes? "
                    "What does the thermostat read vs. what it's set to?"
                ),
                "urgency_level": "standard",
                "next_node_key_map": {
                    "yes": "other_hvac",
                    "no": "other_hvac",
                },
                "is_terminal": False,
            },
            {
                "node_key": "other_hvac",
                "question_text": (
                    "Can you describe the issue in more detail so I can match you with the right tech?"
                ),
                "urgency_level": "standard",
                "is_terminal": True,
            },
        ],
    },
    # ------------------------------------------------------------------
    # Electrical tree
    # ------------------------------------------------------------------
    {
        "trade": "electrical",
        "version": 1,
        "author_credential": "Master Electrician, 20yr",
        "jurisdiction": "ALL",
        "is_active": True,
        "language": "en",
        "nodes": [
            {
                "node_key": "root",
                "question_text": "What's the electrical issue today?",
                "urgency_level": "standard",
                "next_node_key_map": {
                    "spark": "sparking_outlet",
                    "arc": "sparking_outlet",
                    "burn": "burning_smell",
                    "smoke": "burning_smell",
                    "shock": "shock_received",
                    "shocked": "shock_received",
                    "breaker": "tripping_breaker",
                    "trip": "tripping_breaker",
                    "partial": "partial_power",
                    "some power": "partial_power",
                    "other": "other_electrical",
                },
                "is_terminal": False,
            },
            {
                "node_key": "sparking_outlet",
                "question_text": (
                    "This is an electrical emergency. Do not touch the outlet. "
                    "Turn off power at the breaker panel if safe to do so, and call 911."
                ),
                "urgency_level": "emergency_911",
                "is_terminal": True,
            },
            {
                "node_key": "burning_smell",
                "question_text": (
                    "A burning smell from electrical is a fire hazard. "
                    "Please evacuate immediately and call 911. Do not use any switches."
                ),
                "urgency_level": "emergency_911",
                "is_terminal": True,
            },
            {
                "node_key": "shock_received",
                "question_text": (
                    "Someone receiving an electrical shock is a medical emergency. "
                    "Do not touch the person if they are still in contact with the source. "
                    "Call 911 immediately."
                ),
                "urgency_level": "emergency_911",
                "is_terminal": True,
            },
            {
                "node_key": "tripping_breaker",
                "question_text": (
                    "How many times has the breaker tripped, and which circuit is it — "
                    "what appliances or rooms does it serve?"
                ),
                "urgency_level": "standard",
                "coaching_script_id": "breaker_safety_en",
                "next_node_key_map": {
                    "yes": "other_electrical",
                    "no": "other_electrical",
                },
                "is_terminal": False,
            },
            {
                "node_key": "partial_power",
                "question_text": (
                    "Which areas or circuits have lost power? Have you checked your main panel "
                    "and verified the main breaker hasn't tripped?"
                ),
                "urgency_level": "urgent",
                "next_node_key_map": {
                    "yes": "other_electrical",
                    "no": "other_electrical",
                },
                "is_terminal": False,
            },
            {
                "node_key": "other_electrical",
                "question_text": (
                    "Can you describe the electrical issue in more detail? "
                    "I'll get the right licensed electrician scheduled."
                ),
                "urgency_level": "standard",
                "is_terminal": True,
            },
        ],
    },
    # ------------------------------------------------------------------
    # Commercial/Mechanical tree
    # ------------------------------------------------------------------
    {
        "trade": "commercial_mechanical",
        "version": 1,
        "author_credential": "Journeyman Pipefitter, 18yr",
        "jurisdiction": "ALL",
        "is_active": True,
        "language": "en",
        "nodes": [
            {
                "node_key": "root",
                "question_text": (
                    "Is this a commercial or industrial site? What mechanical system needs attention?"
                ),
                "urgency_level": "standard",
                "next_node_key_map": {
                    "boiler": "boiler_lockout",
                    "steam": "steam_trap",
                    "chiller": "chiller_down",
                    "compressed air": "compressed_air",
                    "air compressor": "compressed_air",
                    "other": "other_commercial",
                },
                "is_terminal": False,
            },
            {
                "node_key": "boiler_lockout",
                "question_text": (
                    "Has the boiler gone into lockout — showing an error code or fault light? "
                    "Do you notice any unusual odours or pressure readings?"
                ),
                "urgency_level": "urgent",
                "next_node_key_map": {
                    "unusual odour": "commercial_gas_emergency",
                    "strange smell": "commercial_gas_emergency",
                    "yes": "other_commercial",
                    "no": "other_commercial",
                },
                "is_terminal": False,
            },
            {
                "node_key": "commercial_gas_emergency",
                "question_text": (
                    "A gas smell in a commercial facility is a serious emergency. "
                    "Evacuate the building immediately and call 911 and your gas utility. "
                    "Do not operate any switches or ignition sources."
                ),
                "urgency_level": "emergency_911",
                "is_terminal": True,
            },
            {
                "node_key": "steam_trap",
                "question_text": (
                    "Which steam trap or section of distribution is affected? "
                    "Are you seeing flooding, water hammer, or loss of heating?"
                ),
                "urgency_level": "standard",
                "next_node_key_map": {
                    "yes": "other_commercial",
                    "no": "other_commercial",
                },
                "is_terminal": False,
            },
            {
                "node_key": "chiller_down",
                "question_text": (
                    "Is the chiller in lockout or still attempting to run? "
                    "What is the current building temperature, and are there critical loads at risk?"
                ),
                "urgency_level": "urgent",
                "next_node_key_map": {
                    "yes": "other_commercial",
                    "no": "other_commercial",
                },
                "is_terminal": False,
            },
            {
                "node_key": "compressed_air",
                "question_text": (
                    "Is the compressor completely down or pressure just dropping? "
                    "Which production processes are affected?"
                ),
                "urgency_level": "standard",
                "next_node_key_map": {
                    "yes": "other_commercial",
                    "no": "other_commercial",
                },
                "is_terminal": False,
            },
            {
                "node_key": "other_commercial",
                "question_text": (
                    "Please describe the issue in more detail — site type, system affected, "
                    "and any fault codes — so I can dispatch the right journeyman tech."
                ),
                "urgency_level": "standard",
                "is_terminal": True,
            },
        ],
    },
]


async def seed_triage_data(db: AsyncSession) -> None:
    """
    Idempotent seed of system coaching scripts and triage trees.
    tenant_id=None = system-level, available to all tenants.
    """
    # ------------------------------------------------------------------
    # Coaching scripts
    # ------------------------------------------------------------------
    for script_data in _COACHING_SCRIPTS:
        existing = await db.get(CoachingScript, script_data["id"])
        if existing is None:
            db.add(CoachingScript(**script_data))
            logger.info("triage_seed: inserted coaching script %s", script_data["id"])

    await db.flush()

    # ------------------------------------------------------------------
    # Triage trees + nodes
    # ------------------------------------------------------------------
    for tree_data_orig in _TRIAGE_TREES:
        tree_data = dict(tree_data_orig)  # shallow copy to avoid mutating the module-level list
        nodes_data = tree_data.pop("nodes")

        # Check if a system tree for this trade already exists
        existing_q = await db.execute(
            select(TriageTree).where(
                TriageTree.tenant_id == None,  # noqa: E711
                TriageTree.trade == tree_data["trade"],
                TriageTree.version == tree_data["version"],
            )
        )
        existing_tree = existing_q.scalar_one_or_none()
        if existing_tree is not None:
            logger.debug(
                "triage_seed: system tree already exists trade=%s version=%d",
                tree_data["trade"], tree_data["version"],
            )
            continue

        tree = TriageTree(
            id=uuid.uuid4(),
            tenant_id=None,  # system-level
            **tree_data,
        )
        db.add(tree)
        await db.flush()  # get tree.id

        for node_data in nodes_data:
            node = TriageNode(
                id=uuid.uuid4(),
                tree_id=tree.id,
                next_node_key_map=node_data.get("next_node_key_map", {}),
                coaching_script_id=node_data.get("coaching_script_id"),
                next_node_booked=node_data.get("next_node_booked"),
                urgency_escalation_trigger=node_data.get("urgency_escalation_trigger"),
                question_text_fr=node_data.get("question_text_fr"),
                **{k: v for k, v in node_data.items() if k not in (
                    "next_node_key_map", "coaching_script_id", "next_node_booked",
                    "urgency_escalation_trigger", "question_text_fr",
                )},
            )
            db.add(node)

        await db.flush()
        logger.info("triage_seed: inserted system tree trade=%s version=%d id=%s", tree_data["trade"], tree_data["version"], tree.id)
