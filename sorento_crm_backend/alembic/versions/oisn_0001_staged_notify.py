"""Order inquiry: park reserve requests / reserve notices, send all parked
(`PLAN-oi-staged-notify-14oct.md`, `oi-staged-notify-14oct-acceptance-criteria.md` AC-SN-32).

Additive, nullable, no backfill (NULL = not parked; legacy rows count as sent):
`order_inquiry_reserve_requests.parked_at`, and `order_inquiry_reserve_request_rows`
`notice_parked_at` / `notice_parked_by` / `notified_qty`, plus two partial indexes.

Template data: the seeded reserve-request and reserved-by-CS email templates
gain a bulk branch (`{% if reserve.requests %}` = the short multi-OI email, else today's text)
in `subject` and `layout_json` (and in `body_text`, wrapped so the else branch stays byte for
byte). Updated IN PLACE only while the subject AND the layout still equal the seeded values
(the state after `oirs_0001` + `eml_0002_seed_layouts`); an admin-edited template is left
alone. Downgrade restores the seeded values (only for a row still carrying this revision's
values) and drops the columns.

Hand-written and guarded (`IF NOT EXISTS` / `IF EXISTS`): the shared dev database converges
through `create_all`, and the test suite builds its scratch schema from `Base.metadata`, which
already carries the columns, so upgrade must be a no-op on a schema that has them and
downgrade must tolerate one too. `_schema()`/`_t()` are `oirs_0001_reserve_requests.py`'s own
helper (see its docstring for why): a bare `"projects"` in raw SQL would reach the REAL schema
from a test running on a scratch schema.

The seeded layouts below are frozen copies (a migration must not import app code that keeps
changing).

Revision ID: oisn_0001_staged_notify
Revises: ac_consign_0001_doc_type
Create Date: 2026-10-14
"""
from __future__ import annotations

import copy
import json

import sqlalchemy as sa
from alembic import op

revision = "oisn_0001_staged_notify"
down_revision = "ac_consign_0001_doc_type"
branch_labels = None
depends_on = None

# Built from two halves: several OI tests find THEIR seed migration by scanning alembic/versions
# for the full code as one token (see eml_0002_seed_layouts), and this file must not match.
REQUEST_CODE = "order_inquiry_reserve_requested" + "_default"
RESERVED_CODE = "order_inquiry_reserved" + "_default"

_SEEDED_SUBJECT = {
    REQUEST_CODE: "Reserve request: {{ reserve.inquiry_no }} #{{ reserve.ordinal }} - {{ reserve.so_number }}",
    RESERVED_CODE: "Reserved: {{ reserve.inquiry_no }} #{{ reserve.ordinal }} - {{ reserve.so_number }}",
}

# eml_0002_seed_layouts.SEEDED[...]["layout"], frozen.
_SEEDED_LAYOUT = json.loads(r'''{
 "order_inquiry_reserve_requested": {
  "blocks": [
   {
    "type": "brand_header"
   },
   {
    "text": "Reserve request for {{ reserve.inquiry_no }} #{{ reserve.ordinal }}",
    "type": "heading"
   },
   {
    "text": "{{ requester.name if requester else '-' }} asks CS to reserve stock for {{ reserve.inquiry_no }} #{{ reserve.ordinal }} ({{ reserve.so_number | default(\"\", true) }}).",
    "type": "intro"
   },
   {
    "html": "<table width=\"100%\">\n  <thead>\n    <tr>\n      <th>ITEM CODE</th><th>DELIVERY DATE</th>\n      <th>QTY</th><th>REMAINING</th>\n      <th>REQUESTED</th><th>LOCATION</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for row in reserve.rows %}\n    <tr>\n      <td>{{ row.item_code | default(\"\", true) }}</td>\n      <td>{{ row.delivery_date | default(\"\", true) }}</td>\n      <td>{{ row.qty | default(\"\", true) }}</td>\n      <td>{{ row.remaining | default(\"\", true) }}</td>\n      <td>{{ row.qty_requested | default(\"\", true) }}</td>\n      <td>{{ row.location | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{% if reserve.note %}<p>Note: {{ reserve.note }}</p>{% endif %}\n<p>Requested by {{ requester.name if requester else '-' }}{% if requester %} ({{ requester.email }}){% endif %} on {{ today }}.</p>",
    "type": "custom_text"
   },
   {
    "label": "Open in Order Inquiries",
    "type": "button",
    "url": "{{ reserve.link }}"
   },
   {
    "type": "footer"
   }
  ],
  "version": 1
 },
 "order_inquiry_reserved": {
  "blocks": [
   {
    "type": "brand_header"
   },
   {
    "text": "Stock reserved for {{ reserve.inquiry_no }} #{{ reserve.ordinal }}",
    "type": "heading"
   },
   {
    "text": "{{ actor.name if actor else '-' }} reserved stock for {{ reserve.inquiry_no }} #{{ reserve.ordinal }} ({{ reserve.so_number | default(\"\", true) }}).",
    "type": "intro"
   },
   {
    "html": "<table width=\"100%\">\n  <thead>\n    <tr>\n      <th>ITEM CODE</th><th>QTY</th>\n      <th>REQUESTED</th><th>RESERVED</th>\n      <th>BALANCE</th><th>LOCATION</th>\n      <th>REASON</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for row in reserve.rows %}\n    <tr>\n      <td>{{ row.item_code | default(\"\", true) }}</td>\n      <td>{{ row.qty | default(\"\", true) }}</td>\n      <td>{{ row.qty_requested | default(\"\", true) }}</td>\n      <td>{{ row.qty_reserved | default(\"\", true) }}</td>\n      <td>{{ row.remaining | default(\"\", true) }}</td>\n      <td>{{ row.location | default(\"\", true) }}</td>\n      <td>{{ row.reason | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{% if reserve.open_row_count %}<p>{{ reserve.open_row_count }} line{% if reserve.open_row_count != 1 %}s{% endif %} still to reserve.</p>{% endif %}\n<p>Reserved by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>",
    "type": "custom_text"
   },
   {
    "label": "Open in Order Inquiries",
    "type": "button",
    "url": "{{ reserve.link }}"
   },
   {
    "type": "footer"
   }
  ],
  "version": 1
 }
}''')
_SEEDED_LAYOUT = {
    REQUEST_CODE: _SEEDED_LAYOUT["order_inquiry_reserve_requested"],
    RESERVED_CODE: _SEEDED_LAYOUT["order_inquiry_reserved"],
}

_BULK_TABLE = (
    '<table width="100%">\n  <thead>\n    <tr><th>OI NO</th><th>S/O NO</th><th>CUSTOMER</th>'
    "<th>LINES</th></tr>\n  </thead>\n  <tbody>\n    {% for r in reserve.requests %}\n    <tr>\n"
    '      <td>{{ r.inquiry_no | default("", true) }}</td>\n'
    '      <td>{{ r.so_number | default("", true) }}</td>\n'
    '      <td>{{ r.customer | default("", true) }}</td>\n'
    '      <td>{{ r.line_count | default("", true) }}</td>\n'
    "    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n"
    "{% if reserve.more_count %}<p>and {{ reserve.more_count }} more order "
    "inquir{{ 'y' if reserve.more_count == 1 else 'ies' }}.</p>{% endif %}"
)

_BULK = {
    REQUEST_CODE: {
        "subject": "Reserve requests: {{ reserve.oi_count }} order inquiries "
        "({{ requester.name if requester else '-' }})",
        "heading": "{{ reserve.oi_count }} order inquiries need your reserve",
        "intro": "{{ requester.name if requester else '-' }} asks CS to reserve stock on "
        "{{ reserve.oi_count }} order inquiries ({{ reserve.line_count }} lines).",
        "footer_html": "<p>Requested by {{ requester.name if requester else '-' }} on {{ today }}.</p>",
        "button": "View order inquiries that need your reserve",
        "text": "{{ requester.name if requester else '-' }} asks CS to reserve stock on "
        "{{ reserve.oi_count }} order inquiries ({{ reserve.line_count }} lines).\n\n"
        "OI NO | S/O NO | CUSTOMER | LINES\n"
        "{% for r in reserve.requests %}{{ r.inquiry_no }} | {{ r.so_number }} | {{ r.customer }} | {{ r.line_count }}\n{% endfor %}"
        "{% if reserve.more_count %}and {{ reserve.more_count }} more order "
        "inquir{{ 'y' if reserve.more_count == 1 else 'ies' }}.\n{% endif %}"
        "Requested by {{ requester.name if requester else '-' }} on {{ today }}.\n"
        "View order inquiries that need your reserve: {{ reserve.link }}\n",
    },
    RESERVED_CODE: {
        "subject": "Reserved: {{ reserve.oi_count }} order inquiries "
        "({{ actor.name if actor else '-' }})",
        "heading": "CS answered your reserve requests on {{ reserve.oi_count }} order inquiries",
        "intro": "{{ actor.name if actor else '-' }} reserved stock on "
        "{{ reserve.oi_count }} order inquiries ({{ reserve.line_count }} lines). "
        "Check the balance still to buy.",
        "footer_html": "<p>Reserved by {{ actor.name if actor else '-' }} on {{ today }}.</p>",
        "button": "View reserved order inquiries",
        "text": "{{ actor.name if actor else '-' }} reserved stock on "
        "{{ reserve.oi_count }} order inquiries ({{ reserve.line_count }} lines). "
        "Check the balance still to buy.\n\n"
        "OI NO | S/O NO | CUSTOMER | LINES\n"
        "{% for r in reserve.requests %}{{ r.inquiry_no }} | {{ r.so_number }} | {{ r.customer }} | {{ r.line_count }}\n{% endfor %}"
        "{% if reserve.more_count %}and {{ reserve.more_count }} more order "
        "inquir{{ 'y' if reserve.more_count == 1 else 'ies' }}.\n{% endif %}"
        "Reserved by {{ actor.name if actor else '-' }} on {{ today }}.\n"
        "View reserved order inquiries: {{ reserve.link }}\n",
    },
}

_IF = "{% if reserve.requests %}"
_ELSE = "{% else %}"
_END = "{% endif %}"


def _branch(bulk: str, today: str) -> str:
    return f"{_IF}{bulk}{_ELSE}{today}{_END}"


def _bulk_subject(code: str) -> str:
    return _branch(_BULK[code]["subject"], _SEEDED_SUBJECT[code])


def _bulk_layout(code: str) -> dict:
    layout = copy.deepcopy(_SEEDED_LAYOUT[code])
    spec = _BULK[code]
    for block in layout["blocks"]:
        kind = block["type"]
        if kind == "heading":
            block["text"] = _branch(spec["heading"], block["text"])
        elif kind == "intro":
            block["text"] = _branch(spec["intro"], block["text"])
        elif kind == "custom_text":
            block["html"] = _branch(_BULK_TABLE + spec["footer_html"], block["html"])
        elif kind == "button":
            block["label"] = _branch(spec["button"], block["label"])
    return layout


def _wrap_text(code: str, text: str) -> str:
    return f"{_IF}{_BULK[code]['text']}{_ELSE}{text}{_END}"


def _unwrap_text(code: str, text: str):
    head, tail = f"{_IF}{_BULK[code]['text']}{_ELSE}", _END
    if text.startswith(head) and text.endswith(tail):
        return text[len(head) : -len(tail)]
    return None


def _schema(bind, module: str) -> str:
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return module if current in (None, "public") else f"{current}_{module}"


def upgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    bind.execute(
        sa.text(
            f"""
            ALTER TABLE "{projects}".order_inquiry_reserve_requests
                ADD COLUMN IF NOT EXISTS parked_at TIMESTAMP;
            ALTER TABLE "{projects}".order_inquiry_reserve_request_rows
                ADD COLUMN IF NOT EXISTS notice_parked_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS notice_parked_by VARCHAR(100)
                    REFERENCES users(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS notified_qty NUMERIC(15, 4);
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_reserve_requests_parked
                ON "{projects}".order_inquiry_reserve_requests (parked_at)
                WHERE parked_at IS NOT NULL;
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_reserve_request_rows_notice_parked
                ON "{projects}".order_inquiry_reserve_request_rows (notice_parked_at)
                WHERE notice_parked_at IS NOT NULL;
            """
        )
    )
    # Rows CS already answered count as told: without this, the first amend of an old
    # answer would read as "never told" and park a notice nobody changed. The park guard
    # keeps a re-run from ever marking a still-parked notice as sent.
    bind.execute(
        sa.text(
            f"""
            UPDATE "{projects}".order_inquiry_reserve_request_rows
            SET notified_qty = qty_reserved
            WHERE qty_reserved IS NOT NULL AND notified_qty IS NULL AND notice_parked_at IS NULL
            """
        )
    )
    for code in (REQUEST_CODE, RESERVED_CODE):
        row = bind.execute(
            sa.text(
                "SELECT id, body_text FROM email_templates WHERE code = :code "
                "AND subject = :subject AND layout_json = CAST(:layout AS jsonb)"
            ),
            {
                "code": code,
                "subject": _SEEDED_SUBJECT[code],
                "layout": json.dumps(_SEEDED_LAYOUT[code]),
            },
        ).first()
        if row is None:
            continue
        body_text = row[1]
        bind.execute(
            sa.text(
                "UPDATE email_templates SET subject = :subject, "
                "layout_json = CAST(:layout AS jsonb), body_text = :body_text WHERE id = :id"
            ),
            {
                "subject": _bulk_subject(code),
                "layout": json.dumps(_bulk_layout(code)),
                "body_text": _wrap_text(code, body_text) if body_text and body_text.strip() else body_text,
                "id": row[0],
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    for code in (REQUEST_CODE, RESERVED_CODE):
        row = bind.execute(
            sa.text(
                "SELECT id, body_text FROM email_templates WHERE code = :code "
                "AND subject = :subject AND layout_json = CAST(:layout AS jsonb)"
            ),
            {
                "code": code,
                "subject": _bulk_subject(code),
                "layout": json.dumps(_bulk_layout(code)),
            },
        ).first()
        if row is None:
            continue
        body_text = row[1]
        unwrapped = _unwrap_text(code, body_text) if body_text else None
        bind.execute(
            sa.text(
                "UPDATE email_templates SET subject = :subject, "
                "layout_json = CAST(:layout AS jsonb), body_text = :body_text WHERE id = :id"
            ),
            {
                "subject": _SEEDED_SUBJECT[code],
                "layout": json.dumps(_SEEDED_LAYOUT[code]),
                "body_text": unwrapped if unwrapped is not None else body_text,
                "id": row[0],
            },
        )
    bind.execute(
        sa.text(
            f"""
            DROP INDEX IF EXISTS "{projects}".ix_order_inquiry_reserve_request_rows_notice_parked;
            DROP INDEX IF EXISTS "{projects}".ix_order_inquiry_reserve_requests_parked;
            ALTER TABLE "{projects}".order_inquiry_reserve_request_rows
                DROP COLUMN IF EXISTS notified_qty,
                DROP COLUMN IF EXISTS notice_parked_by,
                DROP COLUMN IF EXISTS notice_parked_at;
            ALTER TABLE "{projects}".order_inquiry_reserve_requests
                DROP COLUMN IF EXISTS parked_at;
            """
        )
    )
