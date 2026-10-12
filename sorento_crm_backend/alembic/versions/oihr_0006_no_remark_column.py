"""Order inquiry handover email without the REMARK column (EMAIL-SO-CHANGES, owner rules
12 Oct 2026, `documentation/plans/scm/PLAN-email-so-line-changes-12oct.md`).

Owner: "nice and sweet". REMARK is gone from the line table; the numbers say what moved
(QTY / NEW QTY, DELIVERY / NEW DATE) and the red headline lists the actions. A line whose
product changed prints the old code in ITEM CODE and the new one in NEW ITEM CODE, a column
pair shown only when some line in the email changed its product, like NEW QTY / NEW DATE. Attachments, which printed inside the
REMARK cell, print as one ``Attachments:`` line under the table. Every other cell, width rule
and the S/O group row are `oihr_0005`'s.

Updates ``email_templates`` (``code`` = ``TEMPLATE_CODE``) IN PLACE only while its
``layout_json`` and ``body_text`` are still exactly what `oihr_0005_print_one_page` wrote; a row
an admin edited is left alone with a logged warning. ``body_html`` is the mirror of the custom
text (#1349 D3). A database with no row gets one inserted. Idempotent. Downgrade restores
`oihr_0005`'s document byte for byte (frozen below).

Revision ID: oihr_0006_no_remark_column
Revises: devf_0002_portal_link_copy
Create Date: 2026-10-12
"""
from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

revision = "oihr_0006_no_remark_column"
down_revision = "devf_0002_portal_link_copy"
branch_labels = None
depends_on = None


# Split, not a literal (`oihr_0001_handover_r2_layout`'s reasoning): several OI tests find
# THEIR seed migration by scanning alembic/versions for the full code as one token.
TEMPLATE_CODE = "order_inquiry_handover" + "_default"

_SUBJECT = "OI: {{ handover.subject_scope }}"


def _mirror(layout: dict) -> str:
    """`email_template_service._mirror_body_html`, restated so a migration never imports
    app code that keeps changing."""
    return "\n".join(
        b["html"] for b in layout["blocks"] if b["type"] == "custom_text" and b.get("html")
    )


# --------------------------------------------------------------------------- #
# The document upgrade writes.                                                 #
# --------------------------------------------------------------------------- #

_LAYOUT = json.loads(r'''{
 "blocks": [
  {
   "type": "brand_header"
  },
  {
   "text": "{{ handover.headline }}",
   "type": "heading"
  },
  {
   "html": "<table width=\"100%\" style=\"font-size:11px;line-height:15px;\">\n  <thead>\n    <tr><th width=\"13%\" style=\"width:13%;white-space:nowrap;padding:4px;\">S/O NO</th><th width=\"13%\" style=\"width:13%;white-space:nowrap;padding:4px;\">SO DATE</th><th width=\"32%\" style=\"width:32%;white-space:nowrap;padding:4px;\">CUSTOMER</th><th width=\"42%\" style=\"width:42%;white-space:nowrap;padding:4px;\">PROJECT</th></tr>\n  </thead>\n  <tbody>\n    {% for order in handover.orders %}\n    <tr>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ order.so_number | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ order.so_date | default(\"\", true) }}</td>\n      <td style=\"padding:4px;\">{{ order.customer | default(\"\", true) }}</td>\n      <td style=\"padding:4px;\">{{ order.project | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set cols = namespace(item_code=false, qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.item_code %}{% set cols.item_code = true %}{% endif -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}\n{%- set span = 4 + (1 if cols.item_code else 0) + (1 if cols.qty else 0) + (1 if cols.delivery_date else 0) -%}\n{%- set grouped = handover.orders | length > 1 -%}\n{%- set total = 16 + 26 + 10 + 15 + (26 if cols.item_code else 0) + (11 if cols.qty else 0) + (15 if cols.delivery_date else 0) -%}\n{%- set w = namespace(loc=(16 * 100) // total, code=(26 * 100) // total, new_code=((26 * 100) // total if cols.item_code else 0), qty=(10 * 100) // total, new_qty=((11 * 100) // total if cols.qty else 0), dd=0, new_dd=0) -%}\n{%- if cols.delivery_date %}{% set w.dd = (15 * 100) // total %}{% set w.new_dd = 100 - w.loc - w.code - w.new_code - w.qty - w.new_qty - w.dd %}{% else %}{% set w.dd = 100 - w.loc - w.code - w.new_code - w.qty - w.new_qty %}{% endif -%}\n<table width=\"100%\" style=\"font-size:11px;line-height:15px;\">\n  <thead style=\"display:table-header-group;\">\n    <tr>\n      <th width=\"{{ w.loc }}%\" style=\"width:{{ w.loc }}%;white-space:nowrap;padding:4px;\">LOCATION</th><th width=\"{{ w.code }}%\" style=\"width:{{ w.code }}%;white-space:nowrap;padding:4px;\">ITEM CODE</th>{% if cols.item_code %}<th width=\"{{ w.new_code }}%\" style=\"width:{{ w.new_code }}%;white-space:nowrap;padding:4px;\">NEW ITEM CODE</th>{% endif %}\n      <th width=\"{{ w.qty }}%\" style=\"width:{{ w.qty }}%;white-space:nowrap;padding:4px;\">QTY</th>{% if cols.qty %}<th width=\"{{ w.new_qty }}%\" style=\"width:{{ w.new_qty }}%;white-space:nowrap;padding:4px;\">NEW QTY</th>{% endif %}\n      <th width=\"{{ w.dd }}%\" style=\"width:{{ w.dd }}%;white-space:nowrap;padding:4px;\">DELIVERY</th>{% if cols.delivery_date %}<th width=\"{{ w.new_dd }}%\" style=\"width:{{ w.new_dd }}%;white-space:nowrap;padding:4px;\">NEW DATE</th>{% endif %}\n    </tr>\n  </thead>\n  <tbody>\n    {% for line in handover.lines %}\n    {% if grouped and (loop.first or line.so_number != loop.previtem.so_number) %}\n    <tr style=\"page-break-inside:avoid;\"><td colspan=\"{{ span }}\" style=\"white-space:nowrap;padding:4px;background-color:#f3f4f6;font-weight:600;\">S/O NO {{ line.so_number | default(\"\", true) }}</td></tr>\n    {% endif %}\n    <tr style=\"page-break-inside:avoid;\">\n      <td style=\"white-space:nowrap;padding:4px;\">{{ line.location | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.item_code %}{{ line.was.item_code }}{% else %}{{ line.item_code | default(\"\", true) }}{% endif %}</td>\n      {% if cols.item_code %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.item_code %}{{ line.item_code | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default(\"\", true) }}{% endif %}</td>\n      {% if cols.qty %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.qty %}{{ line.qty | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>\n      {% if cols.delivery_date %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.delivery_date %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>{% endif %}\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set att = handover.lines | selectattr(\"attachments\") | list -%}\n{% if att %}<p style=\"font-size:11px;\">Attachments: {% for line in att %}{% if not loop.first %}; {% endif %}{{ line.item_code | default(\"\", true) }}: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}<a href=\"{{ a.url }}\">{{ a.name }}</a> (not attached, too large for email){% endif %}{% endfor %}{% endfor %}</p>{% endif %}\n<p>Raised by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>",
   "type": "custom_text"
  },
  {
   "url": "{{ handover.link }}",
   "type": "button",
   "label": "Open in Order Inquiries"
  },
  {
   "type": "footer"
  }
 ],
 "version": 1,
 "width": "print"
}''')

_BODY_HTML = _mirror(_LAYOUT)

_BODY_TEXT = '{%- set cols = namespace(item_code=false, qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.item_code %}{% set cols.item_code = true %}{% endif -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}{%- set grouped = handover.orders | length > 1 -%}{{ handover.headline }}\n\n{% for order in handover.orders %}{{ order.so_number | default("", true) }}{% if order.so_date %} - {{ order.so_date }}{% endif %}{% if order.customer %} - {{ order.customer }}{% endif %}{% if order.project %} - {{ order.project }}{% endif %}\n{% endfor %}\nLOCATION | ITEM CODE |{% if cols.item_code %} NEW ITEM CODE |{% endif %} QTY |{% if cols.qty %} NEW QTY |{% endif %} DELIVERY{% if cols.delivery_date %} | NEW DATE{% endif %}\n{% for line in handover.lines %}{% if grouped and (loop.first or line.so_number != loop.previtem.so_number) %}S/O NO {{ line.so_number | default("", true) }}\n{% endif %}{{ line.location | default("", true) }} | {% if line.was and line.was.item_code %}{{ line.was.item_code }}{% else %}{{ line.item_code | default("", true) }}{% endif %} |{% if cols.item_code %} {% if line.was and line.was.item_code %}{{ line.item_code | default("", true) }}{% endif %} |{% endif %} {% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default("", true) }}{% endif %} |{% if cols.qty %} {% if line.was and line.was.qty %}{{ line.qty | default("", true) }}{% endif %} |{% endif %} {% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default("", true) }}{% endif %}{% if cols.delivery_date %} | {% if line.was and line.was.delivery_date %}{{ line.delivery_date | default("", true) }}{% endif %}{% endif %}\n{% endfor %}{%- set att = handover.lines | selectattr("attachments") | list -%}{% if att %}\nAttachments: {% for line in att %}{% if not loop.first %}; {% endif %}{{ line.item_code | default("", true) }}: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}{{ a.name }} ({{ a.url }}) (not attached, too large for email){% endif %}{% endfor %}{% endfor %}\n{% endif %}\nRaised by {{ actor.name if actor else \'-\' }} on {{ today }}.\nOpen: {{ handover.link }}\n'


# --------------------------------------------------------------------------- #
# Frozen copy of `oihr_0005`'s document, byte for byte: what downgrade restores #
# and the only state upgrade converts.                                         #
# --------------------------------------------------------------------------- #

_PRIOR_LAYOUT = json.loads(r'''{
 "blocks": [
  {
   "type": "brand_header"
  },
  {
   "text": "{{ handover.headline }}",
   "type": "heading"
  },
  {
   "html": "<table width=\"100%\" style=\"font-size:11px;line-height:15px;\">\n  <thead>\n    <tr><th width=\"13%\" style=\"width:13%;white-space:nowrap;padding:4px;\">S/O NO</th><th width=\"13%\" style=\"width:13%;white-space:nowrap;padding:4px;\">SO DATE</th><th width=\"32%\" style=\"width:32%;white-space:nowrap;padding:4px;\">CUSTOMER</th><th width=\"42%\" style=\"width:42%;white-space:nowrap;padding:4px;\">PROJECT</th></tr>\n  </thead>\n  <tbody>\n    {% for order in handover.orders %}\n    <tr>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ order.so_number | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ order.so_date | default(\"\", true) }}</td>\n      <td style=\"padding:4px;\">{{ order.customer | default(\"\", true) }}</td>\n      <td style=\"padding:4px;\">{{ order.project | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}\n{%- set span = 5 + (1 if cols.qty else 0) + (1 if cols.delivery_date else 0) -%}\n{%- set grouped = handover.orders | length > 1 -%}\n{%- if cols.qty and cols.delivery_date %}{% set w = namespace(loc=9, code=19, qty=6, new_qty=9, dd=12, new_dd=12, remark=33) %}\n{%- elif cols.qty %}{% set w = namespace(loc=12, code=24, qty=9, new_qty=11, dd=16, new_dd=0, remark=28) %}\n{%- elif cols.delivery_date %}{% set w = namespace(loc=12, code=23, qty=9, new_qty=0, dd=15, new_dd=15, remark=26) %}\n{%- else %}{% set w = namespace(loc=14, code=28, qty=10, new_qty=0, dd=18, new_dd=0, remark=30) %}{% endif -%}\n<table width=\"100%\" style=\"font-size:11px;line-height:15px;\">\n  <thead style=\"display:table-header-group;\">\n    <tr>\n      <th width=\"{{ w.loc }}%\" style=\"width:{{ w.loc }}%;white-space:nowrap;padding:4px;\">LOCATION</th><th width=\"{{ w.code }}%\" style=\"width:{{ w.code }}%;white-space:nowrap;padding:4px;\">ITEM CODE</th>\n      <th width=\"{{ w.qty }}%\" style=\"width:{{ w.qty }}%;white-space:nowrap;padding:4px;\">QTY</th>{% if cols.qty %}<th width=\"{{ w.new_qty }}%\" style=\"width:{{ w.new_qty }}%;white-space:nowrap;padding:4px;\">NEW QTY</th>{% endif %}\n      <th width=\"{{ w.dd }}%\" style=\"width:{{ w.dd }}%;white-space:nowrap;padding:4px;\">DELIVERY</th>{% if cols.delivery_date %}<th width=\"{{ w.new_dd }}%\" style=\"width:{{ w.new_dd }}%;white-space:nowrap;padding:4px;\">NEW DATE</th>{% endif %}\n      <th width=\"{{ w.remark }}%\" style=\"width:{{ w.remark }}%;white-space:nowrap;padding:4px;\">REMARK</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for line in handover.lines %}\n    {% if grouped and (loop.first or line.so_number != loop.previtem.so_number) %}\n    <tr style=\"page-break-inside:avoid;\"><td colspan=\"{{ span }}\" style=\"white-space:nowrap;padding:4px;background-color:#f3f4f6;font-weight:600;\">S/O NO {{ line.so_number | default(\"\", true) }}</td></tr>\n    {% endif %}\n    <tr style=\"page-break-inside:avoid;\">\n      <td style=\"white-space:nowrap;padding:4px;\">{{ line.location | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ line.item_code | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default(\"\", true) }}{% endif %}</td>\n      {% if cols.qty %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.qty %}{{ line.qty | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>\n      {% if cols.delivery_date %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.delivery_date %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"padding:4px;\">{{ line.remark | default(\"\", true) }}{% if line.attachments %}<br>Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}<a href=\"{{ a.url }}\">{{ a.name }}</a> (not attached, too large for email){% endif %}{% endfor %}{% endif %}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n<p>Raised by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>",
   "type": "custom_text"
  },
  {
   "url": "{{ handover.link }}",
   "type": "button",
   "label": "Open in Order Inquiries"
  },
  {
   "type": "footer"
  }
 ],
 "version": 1,
 "width": "print"
}''')

_PRIOR_BODY_HTML = _mirror(_PRIOR_LAYOUT)

_PRIOR_BODY_TEXT = '{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}{%- set grouped = handover.orders | length > 1 -%}{{ handover.headline }}\n\n{% for order in handover.orders %}{{ order.so_number | default("", true) }}{% if order.so_date %} - {{ order.so_date }}{% endif %}{% if order.customer %} - {{ order.customer }}{% endif %}{% if order.project %} - {{ order.project }}{% endif %}\n{% endfor %}\nLOCATION | ITEM CODE | QTY |{% if cols.qty %} NEW QTY |{% endif %} DELIVERY |{% if cols.delivery_date %} NEW DATE |{% endif %} REMARK\n{% for line in handover.lines %}{% if grouped and (loop.first or line.so_number != loop.previtem.so_number) %}S/O NO {{ line.so_number | default("", true) }}\n{% endif %}{{ line.location | default("", true) }} | {{ line.item_code | default("", true) }} | {% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default("", true) }}{% endif %} |{% if cols.qty %} {% if line.was and line.was.qty %}{{ line.qty | default("", true) }}{% endif %} |{% endif %} {% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default("", true) }}{% endif %} |{% if cols.delivery_date %} {% if line.was and line.was.delivery_date %}{{ line.delivery_date | default("", true) }}{% endif %} |{% endif %} {{ line.remark | default("", true) }}{% if line.attachments %} / Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}{{ a.name }} ({{ a.url }}) (not attached, too large for email){% endif %}{% endfor %}{% endif %}\n{% endfor %}\nRaised by {{ actor.name if actor else \'-\' }} on {{ today }}.\nOpen: {{ handover.link }}\n'


def _set(
    bind, *, layout: dict, body_html: str, body_text: str, only_when: tuple, direction: str
) -> None:
    """Update the row only while its document AND its text part are still `only_when`'s
    (layout, text) pair; log and leave any other row alone. Insert when no row exists."""
    existing = bind.execute(
        sa.text("SELECT id FROM email_templates WHERE code = :code"),
        {"code": TEMPLATE_CODE},
    ).first()
    if existing:
        touched = bind.execute(
            sa.text(
                """
                UPDATE email_templates
                SET layout_json = CAST(:layout AS jsonb), body_html = :body_html,
                    body_text = :body_text
                WHERE code = :code AND layout_json = CAST(:only_when AS jsonb)
                  AND body_text IS NOT DISTINCT FROM :only_when_text
                """
            ),
            {
                "code": TEMPLATE_CODE,
                "layout": json.dumps(layout),
                "body_html": body_html,
                "body_text": body_text,
                "only_when": json.dumps(only_when[0]),
                "only_when_text": only_when[1],
            },
        ).rowcount
        if not touched:
            logger.warning(
                "oihr_0006 %s: email_templates row %s carries a document this migration "
                "did not write (admin-edited, or already converted); left untouched.",
                direction,
                TEMPLATE_CODE,
            )
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO email_templates
                (id, code, name, description, subject, body_html, body_text, layout_json, is_active)
            VALUES
                (gen_random_uuid(), :code, :name, :description, :subject, :body_html,
                 :body_text, CAST(:layout AS jsonb), true)
            """
        ),
        {
            "code": TEMPLATE_CODE,
            "name": "Order Inquiry Handover to Purchasing (default)",
            "description": (
                "Shaped like the manual handover mail CS sends purchasing: SO table with "
                "the SO date, line table (stock location, ITEM CODE / NEW ITEM CODE, QTY / "
                "NEW QTY, DELIVERY / NEW DATE), on the print shell that fits one A4 portrait page."
            ),
            "subject": _SUBJECT,
            "body_html": body_html,
            "body_text": body_text,
            "layout": json.dumps(layout),
        },
    )


def upgrade() -> None:
    _set(
        op.get_bind(),
        layout=_LAYOUT,
        body_html=_BODY_HTML,
        body_text=_BODY_TEXT,
        only_when=(_PRIOR_LAYOUT, _PRIOR_BODY_TEXT),
        direction="upgrade",
    )


def downgrade() -> None:
    _set(
        op.get_bind(),
        layout=_PRIOR_LAYOUT,
        body_html=_PRIOR_BODY_HTML,
        body_text=_PRIOR_BODY_TEXT,
        only_when=(_LAYOUT, _BODY_TEXT),
        direction="downgrade",
    )
