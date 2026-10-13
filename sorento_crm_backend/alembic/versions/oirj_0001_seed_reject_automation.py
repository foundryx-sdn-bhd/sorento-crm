"""Seed the "order inquiry rejected" email to the OI raiser
(PLAN-oi-reject-notify-13oct.md, AC-RJ-6 / AC-RJ-10).

Same method as `undo_0002_seed_undone`, copied not adapted: one template, one automation,
seeded ENABLED and editable on the Automations screen like every other OI mail. The
recipient is the person who raised the inquiry (`include_raiser`), one email per inquiry
per reject action (`one_email`); no roles, so purchasing is not copied on a reject it
made itself.

Idempotent by `email_templates.code` and `(trigger_type, name)`: a re-run SKIPS rows that
already exist, so an admin's own edit is never overwritten.

Revision ID: oirj_0001_seed_reject
Revises: mig_idx_chunks_document_id
Create Date: 2026-10-13
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "oirj_0001_seed_reject"
down_revision = "mig_idx_chunks_document_id"
branch_labels = None
depends_on = None


TEMPLATE_CODE = "order_inquiry_rejected_default"
AUTOMATION_NAME = "Order inquiry rejected"
TRIGGER_TYPE = "order_inquiry_rejected"

_SUBJECT = (
    'OI rejected: {{ reject.inquiry_no }}'
    '{% if reject.so_number %} {{ reject.so_number }}{% endif %}'
)

# Inline, like the handover mail's own body (`oihr_0003_location_column`) - a mail client
# carries no stylesheet.
_TH_STYLE = "border:1px solid #d0d0d5;padding:4px 8px;background:#f2f2f5;text-align:left;"
_TD_STYLE = "border:1px solid #d0d0d5;padding:4px 8px;"
_TABLE_STYLE = "border-collapse:collapse;font-family:Arial, sans-serif;font-size:13px;"

_BODY_HTML = """\
<p style="color:#b91c1c;font-weight:bold;">Rejected: {{ reject.inquiry_no }}</p>
<table style="__TABLE_STYLE__">
  <thead>
    <tr><th style="__TH_STYLE__">OI NO</th><th style="__TH_STYLE__">S/O NO</th><th style="__TH_STYLE__">CUSTOMER</th><th style="__TH_STYLE__">PROJECT</th></tr>
  </thead>
  <tbody>
    <tr>
      <td style="__TD_STYLE__">{{ reject.inquiry_no | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ reject.so_number | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ reject.customer | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ reject.project | default("", true) }}</td>
    </tr>
  </tbody>
</table>
<table style="__TABLE_STYLE__margin-top:12px;">
  <thead>
    <tr><th style="__TH_STYLE__">ITEM CODE</th><th style="__TH_STYLE__">QTY</th><th style="__TH_STYLE__">DELIVERY DATE</th></tr>
  </thead>
  <tbody>
    {% for line in reject.lines %}
    <tr>
      <td style="__TD_STYLE__">{{ line.item_code | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ line.qty | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ line.delivery_date | default("", true) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<p>Reason: {{ reject.reason | default("", true) }}</p>
<p>Rejected{% if actor %} by {{ actor.name }} ({{ actor.email }}){% endif %} on {{ reject.rejected_at }}.</p>
{% if reject.board_link %}<p><a href="{{ reject.board_link }}">Open the sales order on the planning board</a></p>
{% endif %}<p><a href="{{ reject.link }}">Open in Order Inquiries</a></p>
""".replace("__TABLE_STYLE__", _TABLE_STYLE).replace("__TH_STYLE__", _TH_STYLE).replace(
    "__TD_STYLE__", _TD_STYLE
)

_BODY_TEXT = """\
Rejected: {{ reject.inquiry_no }}
OI NO: {{ reject.inquiry_no | default("", true) }}
S/O NO: {{ reject.so_number | default("", true) }}
CUSTOMER: {{ reject.customer | default("", true) }}
PROJECT: {{ reject.project | default("", true) }}

ITEM CODE | QTY | DELIVERY DATE
{% for line in reject.lines %}{{ line.item_code | default("", true) }} | {{ line.qty | default("", true) }} | {{ line.delivery_date | default("", true) }}
{% endfor %}
Reason: {{ reject.reason | default("", true) }}
Rejected{% if actor %} by {{ actor.name }}{% endif %} on {{ reject.rejected_at }}.
{% if reject.board_link %}Planning board: {{ reject.board_link }}
{% endif %}Order inquiry: {{ reject.link }}
"""


def _seed_template(bind) -> None:
    existing = bind.execute(
        sa.text("SELECT id FROM email_templates WHERE code = :code"),
        {"code": TEMPLATE_CODE},
    ).first()
    if existing:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO email_templates (id, code, name, description, subject, body_html, body_text, is_active)
            VALUES (gen_random_uuid(), :code, :name, :description, :subject, :body_html, :body_text, true)
            """
        ),
        {
            "code": TEMPLATE_CODE,
            "name": "Order Inquiry Rejected (default)",
            "description": (
                "Tells the person who raised an order inquiry which of its lines "
                "purchasing rejected, why, by whom and when."
            ),
            "subject": _SUBJECT,
            "body_html": _BODY_HTML,
            "body_text": _BODY_TEXT,
        },
    )


def _seed_automation(bind) -> None:
    existing = bind.execute(
        sa.text("SELECT id FROM automations WHERE trigger_type = :tt AND name = :name"),
        {"tt": TRIGGER_TYPE, "name": AUTOMATION_NAME},
    ).first()
    if existing:
        return
    template_row = bind.execute(
        sa.text("SELECT id FROM email_templates WHERE code = :code"),
        {"code": TEMPLATE_CODE},
    ).first()
    if template_row is None:
        # Should not happen - _seed_template runs first in upgrade().
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO automations (
                id, name, description, enabled,
                trigger_type, trigger_config,
                action_type, email_template_id,
                recipient_config, group_matches,
                schedule_type, timezone
            )
            VALUES (
                gen_random_uuid(), :name, :description, true,
                :trigger_type, '{}'::jsonb,
                'send_email', :template_id,
                CAST(:recipient_config AS jsonb), false,
                'manual', 'Asia/Kuala_Lumpur'
            )
            """
        ),
        {
            "name": AUTOMATION_NAME,
            "description": (
                "Mails the person who raised an order inquiry when purchasing rejects "
                "rows of it: one email per inquiry per reject, every rejected line listed."
            ),
            "trigger_type": TRIGGER_TYPE,
            "template_id": template_row[0],
            "recipient_config": json.dumps(
                {
                    "user_ids": [],
                    "role_ids": [],
                    "extra_emails": [],
                    "include_raiser": True,
                    "one_email": True,
                }
            ),
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    _seed_template(bind)
    _seed_automation(bind)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM automations WHERE trigger_type = :tt AND name = :name"),
        {"tt": TRIGGER_TYPE, "name": AUTOMATION_NAME},
    )
    # Only when nothing else references it: `automations.email_template_id` is
    # `ON DELETE RESTRICT`, and an admin may have pointed another automation at it.
    bind.execute(
        sa.text(
            """
            DELETE FROM email_templates
            WHERE code = :code
              AND NOT EXISTS (
                  SELECT 1 FROM automations
                  WHERE automations.email_template_id = email_templates.id
              )
            """
        ),
        {"code": TEMPLATE_CODE},
    )
