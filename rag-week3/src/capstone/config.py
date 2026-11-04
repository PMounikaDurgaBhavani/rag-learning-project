"""Paths, dates and labels for the capstone. No model, no database here.

AS_OF is the date the demo data is frozen to. Days-since-charge is computed
from each order's charge_date against AS_OF, so the same ticket produces the
same refund decision on any day the app is run (the Week 7 store froze the
day count for the same reason). Set CAPSTONE_AS_OF=YYYY-MM-DD to move it.
"""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "capstone"
SEED_DIR = DATA_DIR / "seed"
STATE_DIR = DATA_DIR / "state"
WEEK12_DIR = ROOT / "week12"
REPORTS_DIR = ROOT / "reports"
FRONTEND_DIST = ROOT / "frontend" / "dist"

MCP_CONFIG = WEEK12_DIR / "mcp_config.json"
MCP_HTTP_PORT = int(os.environ.get("CAPSTONE_MCP_PORT", "8765"))
MCP_HTTP_URL = os.environ.get("CAPSTONE_MCP_URL", f"http://127.0.0.1:{MCP_HTTP_PORT}/mcp")

# Every integration that would be an external system in production is a
# clearly labelled local adapter here. The label travels with every tool
# result and every API response that comes from it.
INTEGRATION_LABELS = {
    "orders": "DEMO ADAPTER: local order store (data/capstone/seed/orders.json); production would call the order service",
    "entitlement": "DEMO ADAPTER: local subscription store (data/capstone/seed/subscriptions.json); production would call the billing/entitlement service",
    "escalation": "DEMO ADAPTER: persistent local escalation queue (data/capstone/state/escalations.jsonl); production would create a ticket in the helpdesk",
    "inbox": "DEMO DATA: tickets seeded from Week 6 eval cases, the Week 7 store and 14 capstone cases; status changes persist in data/capstone/state/",
}

DEPLOYMENT_MODE = os.environ.get("CAPSTONE_MODE", "demo")      # demo | production


def as_of():
    value = os.environ.get("CAPSTONE_AS_OF", "2026-11-04")
    return date.fromisoformat(value)


HELP_CENTRE_PATH = "/app/help-centre/"          # clickable citation target in the dashboard

POLICY_VERSIONS = {
    "current-30-day": {"label": "Current 30-Day", "article_id": "HC-008", "window_days": 30},
    "legacy-14-day": {"label": "Retired 14-Day (legacy terms)", "article_id": "HC-009", "window_days": 14},
}

TIER1_AGENTS = [f"tier1-{name}" for name in (
    "anna", "ben", "chloe", "dev", "ella", "farid", "gia", "hugo",
    "iris", "jonas", "kira", "leo", "maya", "noor")]           # 14 Tier-1 agents (business context)

TIER2_QUEUES = {
    "billing": "tier2-billing",
    "shipping": "tier2-logistics",
    "legal_privacy": "trust-and-safety",
    "security": "tier2-security",
    "general": "tier2-general",
}
