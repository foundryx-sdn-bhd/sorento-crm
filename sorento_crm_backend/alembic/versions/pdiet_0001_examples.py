"""PROMPT-DIET: the `chatbot_examples` table, its synthetic seeds, and the two prompt keys.

Plan: documentation/plans/chatbot/PLAN-prompt-diet-11oct.md.

- `chatbot_examples`: worked parser examples, retrieved by nearest embedding. Seeded with
  invented rows (source 'synthetic', no embedding, so retrieval skips them until
  `scripts/prompt_diet_seed_examples.py --embed` fills them).
- `chatbot_parser_core` and `chatbot_parser_cards`: one version each, text equal to the
  committed data files, production label set only when the key has none. The keys are read
  only while CHATBOT_PROMPT_DIET is on.

Idempotent: `IF NOT EXISTS` and `WHERE NOT EXISTS` throughout.

Revision ID: pdiet_0001_examples
Revises: chat_backfill_0001
"""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.services.prompt_diet_files import PARSER_CARDS_FILE, PARSER_CORE_FILE

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "pdiet_0001_examples"
down_revision = "chat_backfill_0001"
branch_labels = None
depends_on = None

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PROMPTS = (
    ("chatbot_parser_core", PARSER_CORE_FILE, '["current_date"]'),
    ("chatbot_parser_cards", PARSER_CARDS_FILE, "[]"),
)
COMMIT_MESSAGE = "pdiet_0001_examples: prompt diet text, cut from v50"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS chatbot_examples (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    area text NOT NULL,
    message text NOT NULL,
    context text NULL,
    expected_output jsonb NOT NULL,
    embedding vector(1536) NULL,
    source text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamp NOT NULL DEFAULT now()
)
"""

#: The emission keys every seed carries; a seed overrides what its case changes.
BASE_OUTPUT: dict = {
    "message_type": "business_query",
    "intent_hint": None,
    "domain_hint": None,
    "scope_intent": "specific",
    "is_affirmative": False,
    "user_goal": None,
    "continuation": False,
    "access_levels": [],
    "broaden_axis": None,
    "broaden_to": None,
    "date_mode": None,
    "date_filter_start": None,
    "date_filter_end": None,
    "match_mode": "and",
    "demand_qty": None,
    "entities": [],
    "entity_op": "replace_combine",
    "domain_in_message": False,
    "requested_attributes": [],
    "contains_flyer": False,
    "reference_positions": [],
    "reference_target": None,
    "person_mention": None,
    "is_active": None,
    "order_status": None,
    "correction": False,
    "routing": {"suggested_team": None, "suggested_agent": "general_enquiries"},
    "escalation": {"is_escalation_confirmation": False, "company_pick": None},
    "document": [],
    "status": None,
    "asks": [],
    "topic_reset": False,
    "anaphora": {"backward_reference": False},
    "group_by": None,
    "top_n": None,
}


def _product(raw: str) -> dict:
    return {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}


#: (area, message, context, overrides). Every name and code is invented.
SEEDS: list[tuple[str, str, str | None, dict]] = [
    ("master_products", "Do you have the Zorro WC SRT-DEMO-101?", None,
     {"intent_hint": "check_product", "domain_hint": "master_products", "user_goal": "check a product",
      "entities": [_product("SRT-DEMO-101")]}),
    ("master_products", "What size is SRT-DEMO-102?", "Previous reply: SRT-DEMO-102 is in stock.",
     {"intent_hint": "check_product", "domain_hint": "master_products", "user_goal": "ask a product dimension",
      "entities": [_product("SRT-DEMO-102")], "requested_attributes": ["dimension"], "continuation": True}),
    ("master_products", "Which brands do you carry?", None,
     {"intent_hint": "check_product", "domain_hint": "master_products", "user_goal": "list brands"}),
    ("product_attachment", "Send me the certificate for SRT-DEMO-103", None,
     {"intent_hint": "check_product_attachment", "domain_hint": "product_attachment",
      "user_goal": "get a product certificate", "entities": [_product("SRT-DEMO-103")]}),
    ("product_attachment", "Photos of SRT-DEMO-104 please", None,
     {"intent_hint": "check_product_attachment", "domain_hint": "product_attachment",
      "user_goal": "get product photos", "entities": [_product("SRT-DEMO-104")]}),
    ("promotion", "Any promotion this month?", None,
     {"intent_hint": "check_promotion", "domain_hint": "promotion", "user_goal": "see current promotions",
      "date_mode": "overlap"}),
    ("promotion", "Promo for the Zorro range", None,
     {"intent_hint": "check_promotion", "domain_hint": "promotion", "user_goal": "see promotions for a range"}),
    ("inventory", "How many SRT-DEMO-105 in stock?", None,
     {"intent_hint": "check_stock", "domain_hint": "inventory", "user_goal": "check stock of a product",
      "entities": [_product("SRT-DEMO-105")]}),
    ("inventory", "Show me the low stock report", None,
     {"intent_hint": "low_stock_report", "domain_hint": "inventory", "user_goal": "see the low stock report"}),
    ("order", "Where is my order SO-DEMO-0001?", None,
     {"intent_hint": "check_order", "domain_hint": "order", "user_goal": "track an order",
      "document": ["SO-DEMO-0001"], "routing": {"suggested_team": "customer_service", "suggested_agent": "order_enquiries"}}),
    ("order", "Outstanding orders for Test Dealer A", None,
     {"intent_hint": "check_order", "domain_hint": "order", "user_goal": "list outstanding orders",
      "order_status": "outstanding", "status": "outstanding",
      "entities": [{"raw": "Test Dealer A", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]}),
    ("sales", "Sales report for last month", None,
     {"intent_hint": "check_order", "domain_hint": "order", "user_goal": "see a sales report",
      "order_status": "sales_report", "status": "sales_report"}),
    ("sales", "Top selling products this year", None,
     {"intent_hint": "check_order", "domain_hint": "order", "user_goal": "see top selling products",
      "order_status": "top_selling"}),
    ("purchase_order", "Outstanding POs for SRT-DEMO-106", None,
     {"intent_hint": "check_po", "domain_hint": "purchase_order", "user_goal": "list outstanding purchase orders",
      "entities": [_product("SRT-DEMO-106")]}),
    ("spo_allocation", "When did SRT-DEMO-107 last come in?", None,
     {"intent_hint": "check_spo", "domain_hint": "spo_allocation", "user_goal": "find the last receipt",
      "entities": [_product("SRT-DEMO-107")]}),
    ("purchase_cost", "Last purchase cost of SRT-DEMO-108", None,
     {"intent_hint": "check_po_cost", "domain_hint": "purchase_cost", "user_goal": "see the last purchase cost",
      "entities": [_product("SRT-DEMO-108")]}),
    ("resource_attachment", "Do you have the dealer application form?", None,
     {"intent_hint": "check_resource_attachment", "domain_hint": "resource_attachment",
      "user_goal": "get a form"}),
    ("ideate", "I have an idea for a new basin shape", None,
     {"intent_hint": "ideate", "domain_hint": "ideate", "user_goal": "share a product idea"}),
    ("account", "Account 2 of Test Dealer B please", None,
     {"intent_hint": "check_order", "domain_hint": "order", "user_goal": "pick a customer account",
      "entities": [{"raw": "Test Dealer B", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]}),
]


def seed_rows() -> list[dict]:
    return [
        {
            "area": area,
            "message": message,
            "context": context,
            "expected_output": json.dumps({**BASE_OUTPUT, **overrides}, sort_keys=True),
        }
        for area, message, context, overrides in SEEDS
    ]


INSERT_SEED = """
INSERT INTO chatbot_examples (area, message, context, expected_output, source, enabled)
SELECT :area, :message, :context, CAST(:expected_output AS jsonb), 'synthetic', true
WHERE NOT EXISTS (
    SELECT 1 FROM chatbot_examples WHERE source = 'synthetic' AND area = :area AND message = :message
)
"""

INSERT_VERSION = """
INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, commit_message)
SELECT gen_random_uuid(), :name,
       (SELECT COALESCE(MAX(version), 0) + 1 FROM ai_prompt_versions WHERE name = :name),
       'text', :template, CAST(:variables AS jsonb), :message
WHERE NOT EXISTS (SELECT 1 FROM ai_prompt_versions WHERE name = :name AND template = :template)
"""

INSERT_LABEL = """
INSERT INTO ai_prompt_labels (id, name, label, version_id)
SELECT gen_random_uuid(), :name, 'production',
       (SELECT id FROM ai_prompt_versions WHERE name = :name AND template = :template)
WHERE NOT EXISTS (SELECT 1 FROM ai_prompt_labels WHERE name = :name AND label = 'production')
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    bind.execute(sa.text(CREATE_TABLE))
    for row in seed_rows():
        bind.execute(sa.text(INSERT_SEED), row)
    for name, filename, variables in PROMPTS:
        params = {
            "name": name,
            "template": (DATA_DIR / filename).read_text(encoding="utf-8"),
            "variables": variables,
            "message": COMMIT_MESSAGE,
        }
        bind.execute(sa.text(INSERT_VERSION), params)
        bind.execute(sa.text(INSERT_LABEL), params)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP TABLE IF EXISTS chatbot_examples"))
    bind.execute(
        sa.text(
            "DELETE FROM ai_prompt_versions WHERE name IN ('chatbot_parser_core', 'chatbot_parser_cards') "
            "AND commit_message = :m AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": COMMIT_MESSAGE},
    )
