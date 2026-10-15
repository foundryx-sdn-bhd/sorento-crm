"""Order inquiry: request CS to reserve stock (`PLAN-oi-request-cs-reserve.md` 3.1,
`oi-request-cs-reserve-acceptance-criteria.md` AC-RS-17/AC-RS-18).

Two tables (`projects.order_inquiry_reserve_requests` / `..._reserve_request_rows`), a
third link target on `projects.order_inquiry_links` (the CHECK widens from two targets
to three) and two seeded Automation rows (request + reserved mail).

Hand-written and guarded throughout with `IF NOT EXISTS` / `IF EXISTS`, the same shape
`312_project_leads.py` and `421_order_inquiry_links.py` use: the shared dev database
converges through `create_all` rather than `alembic upgrade`
(`sorento_crm_backend/CLAUDE.md`), and the test suite's own `blank_session()` builds its
scratch schema from `Base.metadata` - which already carries both new tables and the
widened CHECK the moment the ORM models exist - so this migration's OWN upgrade() must be
a no-op reaching a database that already has them, not merely a first-run script.

`_schema()`/`_t()` below are `421_order_inquiry_links.py`'s own helper, copied rather
than imported (each migration file carries its own copy, `421`'s own docstring note):
`schema_translate_map` rewrites ORM/`Table` constructs only, so a bare `"projects"` in a
raw `text()` statement would reach the REAL schema from a test running on a scratch
schema. `tests/_pg_fixture.py` names scratch schemas `<default>_projects`, and the
current default schema is enough to tell the two substrates apart.

Downgrade order matters: a `projects.order_inquiry_links` row naming
`reserve_request_row_id` would violate the OLD two-target CHECK the moment it is
restored, so those links are deleted FIRST, then the column, then both new tables, then
the two seeded rows, in that order.

Revision ID: oirs_0001_reserve_requests
Revises: bftp_0001_flows_to_purchasing
Create Date: 2026-09-22
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "oirs_0001_reserve_requests"
down_revision = "bftp_0001_flows_to_purchasing"
branch_labels = None
depends_on = None


REQUEST_TEMPLATE_CODE = "order_inquiry_reserve_requested_default"
RESERVED_TEMPLATE_CODE = "order_inquiry_reserved_default"
REQUEST_TRIGGER = "order_inquiry_reserve_requested"
RESERVED_TRIGGER = "order_inquiry_reserved"
REQUEST_AUTOMATION_NAME = "Order inquiry: request CS to reserve"
RESERVED_AUTOMATION_NAME = "Order inquiry: reserved by CS"

_OLD_CHECK = (
    "(po_line_id IS NOT NULL)::int + (spo_allocation_id IS NOT NULL)::int = 1"
)
_NEW_CHECK = (
    "(po_line_id IS NOT NULL)::int + (spo_allocation_id IS NOT NULL)::int"
    " + (reserve_request_row_id IS NOT NULL)::int = 1"
)

_TH_STYLE = "border:1px solid #d0d0d5;padding:4px 8px;background:#f2f2f5;text-align:left;"
_TD_STYLE = "border:1px solid #d0d0d5;padding:4px 8px;"
_TABLE_STYLE = "border-collapse:collapse;font-family:Arial, sans-serif;font-size:13px;"

_REQUEST_SUBJECT = (
    "Reserve request: {{ reserve.inquiry_no }} #{{ reserve.ordinal }} - {{ reserve.so_number }}"
)
_RESERVED_SUBJECT = (
    "Reserved: {{ reserve.inquiry_no }} #{{ reserve.ordinal }} - {{ reserve.so_number }}"
)

_REQUEST_BODY_HTML = """\
<p>{{ requester.name if requester else '-' }} asks CS to reserve stock for
{{ reserve.inquiry_no }} #{{ reserve.ordinal }} ({{ reserve.so_number | default("", true) }}).</p>
<table style="__TABLE_STYLE__">
  <thead>
    <tr>
      <th style="__TH_STYLE__">ITEM CODE</th><th style="__TH_STYLE__">DELIVERY DATE</th>
      <th style="__TH_STYLE__">QTY</th><th style="__TH_STYLE__">REMAINING</th>
      <th style="__TH_STYLE__">REQUESTED</th><th style="__TH_STYLE__">LOCATION</th>
    </tr>
  </thead>
  <tbody>
    {% for row in reserve.rows %}
    <tr>
      <td style="__TD_STYLE__">{{ row.item_code | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.delivery_date | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.qty | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.remaining | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.qty_requested | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.location | default("", true) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% if reserve.note %}<p>Note: {{ reserve.note }}</p>{% endif %}
<p>Requested by {{ requester.name if requester else '-' }}{% if requester %} ({{ requester.email }}){% endif %} on {{ today }}.</p>
<p><a href="{{ reserve.link }}">Open in Order Inquiries</a></p>
""".replace("__TABLE_STYLE__", _TABLE_STYLE).replace("__TH_STYLE__", _TH_STYLE).replace(
    "__TD_STYLE__", _TD_STYLE
)

_REQUEST_BODY_TEXT = """\
{{ requester.name if requester else '-' }} asks CS to reserve stock for {{ reserve.inquiry_no }} #{{ reserve.ordinal }} ({{ reserve.so_number | default("", true) }}).

ITEM CODE | DELIVERY DATE | QTY | REMAINING | REQUESTED | LOCATION
{% for row in reserve.rows %}{{ row.item_code | default("", true) }} | {{ row.delivery_date | default("", true) }} | {{ row.qty | default("", true) }} | {{ row.remaining | default("", true) }} | {{ row.qty_requested | default("", true) }} | {{ row.location | default("", true) }}
{% endfor %}
{% if reserve.note %}Note: {{ reserve.note }}
{% endif %}Requested by {{ requester.name if requester else '-' }} on {{ today }}.
Open: {{ reserve.link }}
"""

_RESERVED_BODY_HTML = """\
<p>{{ actor.name if actor else '-' }} reserved stock for
{{ reserve.inquiry_no }} #{{ reserve.ordinal }} ({{ reserve.so_number | default("", true) }}).</p>
<table style="__TABLE_STYLE__">
  <thead>
    <tr>
      <th style="__TH_STYLE__">ITEM CODE</th><th style="__TH_STYLE__">QTY</th>
      <th style="__TH_STYLE__">REQUESTED</th><th style="__TH_STYLE__">RESERVED</th>
      <th style="__TH_STYLE__">BALANCE</th><th style="__TH_STYLE__">LOCATION</th>
      <th style="__TH_STYLE__">REASON</th>
    </tr>
  </thead>
  <tbody>
    {% for row in reserve.rows %}
    <tr>
      <td style="__TD_STYLE__">{{ row.item_code | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.qty | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.qty_requested | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.qty_reserved | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.remaining | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.location | default("", true) }}</td>
      <td style="__TD_STYLE__">{{ row.reason | default("", true) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<p>Reserved by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>
<p><a href="{{ reserve.link }}">Open in Order Inquiries</a></p>
""".replace("__TABLE_STYLE__", _TABLE_STYLE).replace("__TH_STYLE__", _TH_STYLE).replace(
    "__TD_STYLE__", _TD_STYLE
)

_RESERVED_BODY_TEXT = """\
{{ actor.name if actor else '-' }} reserved stock for {{ reserve.inquiry_no }} #{{ reserve.ordinal }} ({{ reserve.so_number | default("", true) }}).

ITEM CODE | QTY | REQUESTED | RESERVED | BALANCE | LOCATION | REASON
{% for row in reserve.rows %}{{ row.item_code | default("", true) }} | {{ row.qty | default("", true) }} | {{ row.qty_requested | default("", true) }} | {{ row.qty_reserved | default("", true) }} | {{ row.remaining | default("", true) }} | {{ row.location | default("", true) }} | {{ row.reason | default("", true) }}
{% endfor %}
Reserved by {{ actor.name if actor else '-' }} on {{ today }}.
Open: {{ reserve.link }}
"""


def _schema(bind, module: str) -> str:
    """Where a module's tables live, for RAW SQL, under BOTH substrates - copied from
    `421_order_inquiry_links.py`'s own helper (see that file's docstring for why)."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return module if current in (None, "public") else f"{current}_{module}"


def _t(bind, name: str) -> str:
    return f'"{_schema(bind, "projects")}"."{name}"'


def _create_tables(bind) -> None:
    projects = _schema(bind, "projects")
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS "{projects}".order_inquiry_reserve_requests (
                id UUID PRIMARY KEY,
                company_id UUID REFERENCES companies(id),
                order_inquiry_id UUID NOT NULL
                    REFERENCES "{projects}".order_inquiries(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                state VARCHAR(16) NOT NULL DEFAULT 'requested'
                    CONSTRAINT ck_order_inquiry_reserve_requests_state
                    CHECK (state IN ('requested', 'reserved', 'cancelled')),
                requested_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
                requested_at TIMESTAMP NOT NULL DEFAULT now(),
                note TEXT,
                reserved_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
                reserved_at TIMESTAMP,
                cancelled_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
                cancelled_at TIMESTAMP,
                CONSTRAINT uq_order_inquiry_reserve_requests_ordinal
                    UNIQUE (order_inquiry_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_reserve_requests_inquiry
                ON "{projects}".order_inquiry_reserve_requests (order_inquiry_id);

            CREATE TABLE IF NOT EXISTS "{projects}".order_inquiry_reserve_request_rows (
                id UUID PRIMARY KEY,
                company_id UUID REFERENCES companies(id),
                request_id UUID NOT NULL
                    REFERENCES "{projects}".order_inquiry_reserve_requests(id) ON DELETE CASCADE,
                row_id UUID NOT NULL
                    REFERENCES "{projects}".order_inquiry_rows(id) ON DELETE CASCADE,
                qty_requested NUMERIC(15, 4) NOT NULL
                    CONSTRAINT ck_order_inquiry_reserve_rows_qty_positive CHECK (qty_requested > 0),
                warehouse_id UUID REFERENCES warehouses(id) ON DELETE SET NULL,
                qty_reserved NUMERIC(15, 4),
                reason TEXT,
                CONSTRAINT uq_order_inquiry_reserve_request_rows_row
                    UNIQUE (request_id, row_id)
            );
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_reserve_request_rows_request
                ON "{projects}".order_inquiry_reserve_request_rows (request_id);
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_reserve_request_rows_row
                ON "{projects}".order_inquiry_reserve_request_rows (row_id);

            ALTER TABLE "{projects}".order_inquiry_links
                ADD COLUMN IF NOT EXISTS reserve_request_row_id UUID;
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_links_reserve_request_row
                ON "{projects}".order_inquiry_links (reserve_request_row_id);
            """
        )
    )
    # `ON DELETE CASCADE`, NOT `SET NULL` (B2, security review round 2, amended in
    # place - unmerged, so no database outside this lane's own has applied the OLD
    # SET NULL constraint at all). A SET NULL onto this column is not self-resolving:
    # `ck_order_inquiry_links_one_target` requires EXACTLY ONE of the link's three
    # targets set at all times, and a reserve link's other two are already null, so
    # nulling this one leaves the CHECK satisfying none of them. `DROP CONSTRAINT IF
    # EXISTS` + `ADD CONSTRAINT`, named explicitly (matching `app/models/project_so.py`),
    # rather than a bare `ADD COLUMN ... REFERENCES`, so a re-run of this migration
    # against a database that already carries the column still lands on CASCADE.
    bind.execute(
        sa.text(
            f"""
            ALTER TABLE "{projects}".order_inquiry_links
                DROP CONSTRAINT IF EXISTS fk_order_inquiry_links_reserve_request_row;
            ALTER TABLE "{projects}".order_inquiry_links
                ADD CONSTRAINT fk_order_inquiry_links_reserve_request_row
                    FOREIGN KEY (reserve_request_row_id)
                    REFERENCES "{projects}".order_inquiry_reserve_request_rows(id)
                    ON DELETE CASCADE;
            """
        )
    )


def _widen_check(bind) -> None:
    links = _t(bind, "order_inquiry_links")
    bind.execute(
        sa.text(
            f"ALTER TABLE {links} DROP CONSTRAINT IF EXISTS ck_order_inquiry_links_one_target"
        )
    )
    bind.execute(
        sa.text(
            f"ALTER TABLE {links} ADD CONSTRAINT ck_order_inquiry_links_one_target "
            f"CHECK ({_NEW_CHECK})"
        )
    )


def _restore_check(bind) -> None:
    links = _t(bind, "order_inquiry_links")
    bind.execute(
        sa.text(
            f"ALTER TABLE {links} DROP CONSTRAINT IF EXISTS ck_order_inquiry_links_one_target"
        )
    )
    bind.execute(
        sa.text(
            f"ALTER TABLE {links} ADD CONSTRAINT ck_order_inquiry_links_one_target "
            f"CHECK ({_OLD_CHECK})"
        )
    )


def _seed_template(bind, code: str, name: str, description: str, subject: str,
                    body_html: str, body_text: str) -> None:
    existing = bind.execute(
        sa.text("SELECT id FROM email_templates WHERE code = :code"), {"code": code}
    ).first()
    if existing:
        # Skip, not UPDATE (same ruling `oihe_0001_seed_handover_automation.py` and
        # `212_seed_pr_sponsorship_approved_automation.py` take): Alembic never re-runs
        # an applied revision, so this branch is only reached by a genuine re-run, and an
        # UPDATE there could only overwrite an admin's own hand edit.
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO email_templates (id, code, name, description, subject, body_html, body_text, is_active)
            VALUES (gen_random_uuid(), :code, :name, :description, :subject, :body_html, :body_text, true)
            """
        ),
        {
            "code": code,
            "name": name,
            "description": description,
            "subject": subject,
            "body_html": body_html,
            "body_text": body_text,
        },
    )


def _seed_automation(bind, *, trigger_type: str, name: str, description: str,
                      template_code: str, recipient_config: dict) -> None:
    existing = bind.execute(
        sa.text("SELECT id FROM automations WHERE trigger_type = :tt AND name = :n"),
        {"tt": trigger_type, "n": name},
    ).first()
    if existing:
        return
    template_row = bind.execute(
        sa.text("SELECT id FROM email_templates WHERE code = :code"),
        {"code": template_code},
    ).first()
    if template_row is None:
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
            "name": name,
            "description": description,
            "trigger_type": trigger_type,
            "template_id": template_row[0],
            "recipient_config": json.dumps(recipient_config),
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_tables(bind)
    _widen_check(bind)
    _seed_template(
        bind,
        REQUEST_TEMPLATE_CODE,
        "Order Inquiry Reserve Request (default)",
        "Asks CS to reserve stock for one or more order inquiry rows.",
        _REQUEST_SUBJECT,
        _REQUEST_BODY_HTML,
        _REQUEST_BODY_TEXT,
    )
    _seed_template(
        bind,
        RESERVED_TEMPLATE_CODE,
        "Order Inquiry Reserved by CS (default)",
        "Tells the requester what CS reserved, and the balance still to buy.",
        _RESERVED_SUBJECT,
        _RESERVED_BODY_HTML,
        _RESERVED_BODY_TEXT,
    )
    _seed_automation(
        bind,
        trigger_type=REQUEST_TRIGGER,
        name=REQUEST_AUTOMATION_NAME,
        description=(
            "Mails CS (Eling) whenever purchasing asks for stock to be reserved against "
            "one or more order inquiry rows, Cc the requester and the person who raised "
            "the inquiry. `user_ids` is empty at seed time - the owner adds Eling after "
            "deploy."
        ),
        template_code=REQUEST_TEMPLATE_CODE,
        recipient_config={
            "user_ids": [],
            "role_ids": [],
            "extra_emails": [],
            "include_actor": True,
            "include_raiser": True,
            "one_email": True,
        },
    )
    _seed_automation(
        bind,
        trigger_type=RESERVED_TRIGGER,
        name=RESERVED_AUTOMATION_NAME,
        description=(
            "Mails the requester once CS confirms what was reserved, Cc the person who "
            "raised the inquiry."
        ),
        template_code=RESERVED_TEMPLATE_CODE,
        recipient_config={
            "user_ids": [],
            "role_ids": [],
            "extra_emails": [],
            "include_requester": True,
            "include_raiser": True,
            "one_email": True,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    # Named, not just by trigger_type: an admin could have cloned either seeded
    # automation onto the SAME trigger with a name of their own, and a bare
    # `trigger_type IN (...)` would delete that row too (review round nit).
    bind.execute(
        sa.text(
            "DELETE FROM automations WHERE trigger_type IN (:t1, :t2) AND name IN (:n1, :n2)"
        ),
        {
            "t1": REQUEST_TRIGGER,
            "t2": RESERVED_TRIGGER,
            "n1": REQUEST_AUTOMATION_NAME,
            "n2": RESERVED_AUTOMATION_NAME,
        },
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM email_templates
            WHERE code IN (:c1, :c2)
              AND NOT EXISTS (
                  SELECT 1 FROM automations WHERE automations.email_template_id = email_templates.id
              )
            """
        ),
        {"c1": REQUEST_TEMPLATE_CODE, "c2": RESERVED_TEMPLATE_CODE},
    )
    # A reserve-only link would violate the OLD two-target CHECK the instant it is
    # restored below, so those links go first.
    bind.execute(
        sa.text(
            f'DELETE FROM "{projects}".order_inquiry_links WHERE reserve_request_row_id IS NOT NULL'
        )
    )
    _restore_check(bind)
    bind.execute(
        sa.text(
            f'ALTER TABLE "{projects}".order_inquiry_links DROP COLUMN IF EXISTS reserve_request_row_id'
        )
    )
    bind.execute(
        sa.text(f'DROP TABLE IF EXISTS "{projects}".order_inquiry_reserve_request_rows')
    )
    bind.execute(
        sa.text(f'DROP TABLE IF EXISTS "{projects}".order_inquiry_reserve_requests')
    )
