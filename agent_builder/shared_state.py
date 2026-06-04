"""
Cross-module session state.

Why this exists: ADK's InMemorySessionService does not reliably persist
session.state mutations across get_session() calls in our deployment
(see comment in main.py:46-52). The agent uses module-level dicts as
the source of truth instead.

This module is intentionally a LEAF — it imports nothing project-internal,
so main.py, agent_definition.py, and followup.py can import it freely.

Contracts:
    PROFILE_BY_SESSION: dict[str, dict] (S2')
        Validated intake profile snapshot keyed by ADK session_id.
        Written by main.py at intake-completion handoff (one write per session).
        Read by agent_definition.py.search_products to inject product_type.

    TOP3_BY_SESSION: dict[str, list[dict]] (S3)
        Enriched top-3 product dicts (post-rank, post-mojibake-fix) keyed by
        ADK session_id. Each entry is a list of <=3 product dicts as the FE
        would receive them — same shape as the response.top3 array.
        Written by main.py AFTER top3_enriched is built (one write per pipeline run).
        Read by followup.py for deterministic single-product voice text + ordinal refs.

    CONTACT_BY_SESSION: dict[str, dict] (T3)
        Contact-capture state per session keyed by ADK session_id.
        Schema per session:
            {"state": "NONE"|"ASKED"|"AWAITING_EMAIL"|"CAPTURED"|"DECLINED",
             "email": str|None,
             "invalid_attempts": int}
        Written by main.py at three sites:
            1. After first recommendation render (NONE → ASKED + suffix appended).
            2. In the else-branch FSM (ASKED → AWAITING_EMAIL/DECLINED;
               AWAITING_EMAIL → CAPTURED/AWAITING_EMAIL/DECLINED).
            3. On reset / done-intent (popped/cleared).
        PII discipline: full email lives ONLY in this dict and in the user-facing
        voice text. Logs use _email_domain() per followup.py §5.8.
        See SPEC v2 §5 for state-transition table.

Lifecycle: process-lifetime. For multi-instance deploys this would need
Firestore; hackathon runs with --max-instances=1 so RAM is sufficient.

Eviction: none for hackathon. ~3KB per session * --max-instances=1 * realistic
session count (<1000) = bounded. If we cross 10k sessions per instance, add a
simple LRU. Not in scope for Day 6.
"""

PROFILE_BY_SESSION: dict = {}
TOP3_BY_SESSION: dict = {}

# T3 — Contact capture state per session.
# Schema per session:
#   {"state": "NONE"|"ASKED"|"AWAITING_EMAIL"|"CAPTURED"|"DECLINED",
#    "email": str|None, "invalid_attempts": int}
CONTACT_BY_SESSION: dict = {}
