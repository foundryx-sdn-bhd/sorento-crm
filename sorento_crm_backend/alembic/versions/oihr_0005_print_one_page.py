"""Order inquiry handover email that prints on one A4 portrait page, one line per cell
(OI-EMAIL-PRINT, `documentation/plans/scm/PLAN-oi-email-print-09oct.md`, AC-P4 to AC-P11).

Owner + end user (9 Oct, SO418869): "The form can fit in one page to print out?" and
"avoid multiple lines inside one cell". On `oihr_0004`'s document SO DATE and S/O NO
repeated on every line, and the 900px `wide` card is wider than A4 portrait's printable
width (698px at Outlook's 0.5in margins).

Updates ``email_templates`` (``code`` = ``TEMPLATE_CODE`` below) IN PLACE, only while its
``layout_json`` and ``body_text`` are still one of the known UNEDITED states
(``_UNEDITED_STATES``): what `oihr_0004_wide_line_table` wrote, or the pre-0004 document a
database still holds when oihr_0004's own guarded update matched nothing there. A row an
admin edited in the block editor is theirs and is left alone, with a logged warning. The
new document:

* ``width: "print"`` (672px card, 12px gutters, `app/services/email_layout.py`).
* S/O table gains SO DATE (``order.so_date``, which `_build_handover_context` now carries).
* line table drops SO DATE and S/O NO; when the email names two or more orders, a one-cell
  group row ``S/O NO <n>`` opens each order's lines instead. Short labels (NEW QTY,
  DELIVERY, NEW DATE), 12px text, ``white-space:nowrap`` on every header and every
  cell but REMARK, ``<thead>`` repeated on each printed page.

``body_html`` becomes the mirror of the custom text (#1349 D3) and ``body_text`` the same
change in the pipe table. Subject untouched. A database with no row gets one inserted.
Idempotent. Downgrade restores `oihr_0004`'s three values byte for byte (frozen below),
whichever unedited state the upgrade started from.

Revision ID: oihr_0005_print_one_page
Revises: bflo_0001_borrow_order_toggle
Create Date: 2026-10-09
"""
from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

revision = "oihr_0005_print_one_page"
down_revision = "bflo_0001_borrow_order_toggle"
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
# The PRINT document: what upgrade writes.                                     #
# --------------------------------------------------------------------------- #

_PRINT_LAYOUT = json.loads(r'''{
 "blocks": [
  {
   "type": "brand_header"
  },
  {
   "text": "{{ handover.headline }}",
   "type": "heading"
  },
  {
   "html": "<table width=\"100%\" style=\"font-size:12px;line-height:16px;\">\n  <thead>\n    <tr><th width=\"18%\" style=\"width:18%;white-space:nowrap;padding:4px;\">S/O NO</th><th width=\"16%\" style=\"width:16%;white-space:nowrap;padding:4px;\">SO DATE</th><th width=\"40%\" style=\"width:40%;white-space:nowrap;padding:4px;\">CUSTOMER</th><th width=\"26%\" style=\"width:26%;white-space:nowrap;padding:4px;\">PROJECT</th></tr>\n  </thead>\n  <tbody>\n    {% for order in handover.orders %}\n    <tr>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ order.so_number | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ order.so_date | default(\"\", true) }}</td>\n      <td style=\"padding:4px;\">{{ order.customer | default(\"\", true) }}</td>\n      <td style=\"padding:4px;\">{{ order.project | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}\n{%- set span = 5 + (1 if cols.qty else 0) + (1 if cols.delivery_date else 0) -%}\n{%- set grouped = handover.orders | length > 1 -%}\n{%- if cols.qty and cols.delivery_date %}{% set w = namespace(loc=9, code=19, qty=6, new_qty=9, dd=12, new_dd=12, remark=33) %}\n{%- elif cols.qty %}{% set w = namespace(loc=12, code=24, qty=9, new_qty=11, dd=16, new_dd=0, remark=28) %}\n{%- elif cols.delivery_date %}{% set w = namespace(loc=12, code=23, qty=9, new_qty=0, dd=15, new_dd=15, remark=26) %}\n{%- else %}{% set w = namespace(loc=14, code=28, qty=10, new_qty=0, dd=18, new_dd=0, remark=30) %}{% endif -%}\n<table width=\"100%\" style=\"font-size:11px;line-height:15px;\">\n  <thead style=\"display:table-header-group;\">\n    <tr>\n      <th width=\"{{ w.loc }}%\" style=\"width:{{ w.loc }}%;white-space:nowrap;padding:4px;\">LOCATION</th><th width=\"{{ w.code }}%\" style=\"width:{{ w.code }}%;white-space:nowrap;padding:4px;\">ITEM CODE</th>\n      <th width=\"{{ w.qty }}%\" style=\"width:{{ w.qty }}%;white-space:nowrap;padding:4px;\">QTY</th>{% if cols.qty %}<th width=\"{{ w.new_qty }}%\" style=\"width:{{ w.new_qty }}%;white-space:nowrap;padding:4px;\">NEW QTY</th>{% endif %}\n      <th width=\"{{ w.dd }}%\" style=\"width:{{ w.dd }}%;white-space:nowrap;padding:4px;\">DELIVERY</th>{% if cols.delivery_date %}<th width=\"{{ w.new_dd }}%\" style=\"width:{{ w.new_dd }}%;white-space:nowrap;padding:4px;\">NEW DATE</th>{% endif %}\n      <th width=\"{{ w.remark }}%\" style=\"width:{{ w.remark }}%;white-space:nowrap;padding:4px;\">REMARK</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for line in handover.lines %}\n    {% if grouped and (loop.first or line.so_number != loop.previtem.so_number) %}\n    <tr style=\"page-break-inside:avoid;\"><td colspan=\"{{ span }}\" style=\"white-space:nowrap;padding:4px;background-color:#f3f4f6;font-weight:600;\">S/O NO {{ line.so_number | default(\"\", true) }}</td></tr>\n    {% endif %}\n    <tr style=\"page-break-inside:avoid;\">\n      <td style=\"white-space:nowrap;padding:4px;\">{{ line.location | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{{ line.item_code | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default(\"\", true) }}{% endif %}</td>\n      {% if cols.qty %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.qty %}{{ line.qty | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>\n      {% if cols.delivery_date %}<td style=\"white-space:nowrap;padding:4px;\">{% if line.was and line.was.delivery_date %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"padding:4px;\">{{ line.remark | default(\"\", true) }}{% if line.attachments %}<br>Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}<a href=\"{{ a.url }}\">{{ a.name }}</a> (not attached, too large for email){% endif %}{% endfor %}{% endif %}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n<p>Raised by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>",
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

_PRINT_BODY_HTML = _mirror(_PRINT_LAYOUT)

_PRINT_BODY_TEXT = '{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}{%- set grouped = handover.orders | length > 1 -%}{{ handover.headline }}\n\n{% for order in handover.orders %}{{ order.so_number | default("", true) }}{% if order.so_date %} - {{ order.so_date }}{% endif %}{% if order.customer %} - {{ order.customer }}{% endif %}{% if order.project %} - {{ order.project }}{% endif %}\n{% endfor %}\nLOCATION | ITEM CODE | QTY |{% if cols.qty %} NEW QTY |{% endif %} DELIVERY |{% if cols.delivery_date %} NEW DATE |{% endif %} REMARK\n{% for line in handover.lines %}{% if grouped and (loop.first or line.so_number != loop.previtem.so_number) %}S/O NO {{ line.so_number | default("", true) }}\n{% endif %}{{ line.location | default("", true) }} | {{ line.item_code | default("", true) }} | {% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default("", true) }}{% endif %} |{% if cols.qty %} {% if line.was and line.was.qty %}{{ line.qty | default("", true) }}{% endif %} |{% endif %} {% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default("", true) }}{% endif %} |{% if cols.delivery_date %} {% if line.was and line.was.delivery_date %}{{ line.delivery_date | default("", true) }}{% endif %} |{% endif %} {{ line.remark | default("", true) }}{% if line.attachments %} / Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}{{ a.name }} ({{ a.url }}) (not attached, too large for email){% endif %}{% endfor %}{% endif %}\n{% endfor %}\nRaised by {{ actor.name if actor else \'-\' }} on {{ today }}.\nOpen: {{ handover.link }}\n'


# --------------------------------------------------------------------------- #
# Frozen copies of `oihr_0004`'s row, byte for byte: what downgrade restores.  #
# Never edit these to "fix" them.                                              #
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
   "html": "<table width=\"100%\">\n  <thead>\n    <tr><th style=\"padding:6px 8px;\">S/O NO</th><th style=\"padding:6px 8px;\">CUSTOMER</th><th style=\"padding:6px 8px;\">PROJECT</th></tr>\n  </thead>\n  <tbody>\n    {% for order in handover.orders %}\n    <tr>\n      <td style=\"padding:6px 8px;\">{{ order.so_number | default(\"\", true) }}</td>\n      <td style=\"padding:6px 8px;\">{{ order.customer | default(\"\", true) }}</td>\n      <td style=\"padding:6px 8px;\">{{ order.project | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}<table width=\"100%\">\n  <thead>\n    <tr>\n      <th style=\"width:86px;padding:6px 8px;\">SO DATE</th><th style=\"width:82px;padding:6px 8px;\">S/O NO</th><th style=\"width:92px;padding:6px 8px;\">LOCATION</th><th style=\"width:140px;padding:6px 8px;\">ITEM CODE</th>\n      <th style=\"width:56px;padding:6px 8px;\">QTY</th>{% if cols.qty %}<th style=\"width:72px;padding:6px 8px;\">QTY CHANGE TO</th>{% endif %}\n      <th style=\"width:92px;padding:6px 8px;\">DELIVERY DATE</th>{% if cols.delivery_date %}<th style=\"width:92px;padding:6px 8px;\">DELIVERY DATE CHANGE TO</th>{% endif %}\n      <th style=\"padding:6px 8px;\">REMARK</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for line in handover.lines %}\n    <tr>\n      <td style=\"white-space:nowrap;padding:6px 8px;\">{{ line.so_date | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:6px 8px;\">{{ line.so_number | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:6px 8px;\">{{ line.location | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:6px 8px;\">{{ line.item_code | default(\"\", true) }}</td>\n      <td style=\"white-space:nowrap;padding:6px 8px;\">{% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default(\"\", true) }}{% endif %}</td>\n      {% if cols.qty %}<td style=\"white-space:nowrap;padding:6px 8px;\">{% if line.was and line.was.qty %}{{ line.qty | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"white-space:nowrap;padding:6px 8px;\">{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>\n      {% if cols.delivery_date %}<td style=\"white-space:nowrap;padding:6px 8px;\">{% if line.was and line.was.delivery_date %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td style=\"padding:6px 8px;\">{{ line.remark | default(\"\", true) }}{% if line.attachments %}<br>Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}<a href=\"{{ a.url }}\">{{ a.name }}</a> (not attached, too large for email){% endif %}{% endfor %}{% endif %}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n<p>Raised by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>",
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
 "width": "wide"
}''')

_PRIOR_BODY_HTML = '<table width="100%">\n  <thead>\n    <tr><th style="padding:6px 8px;">S/O NO</th><th style="padding:6px 8px;">CUSTOMER</th><th style="padding:6px 8px;">PROJECT</th></tr>\n  </thead>\n  <tbody>\n    {% for order in handover.orders %}\n    <tr>\n      <td style="padding:6px 8px;">{{ order.so_number | default("", true) }}</td>\n      <td style="padding:6px 8px;">{{ order.customer | default("", true) }}</td>\n      <td style="padding:6px 8px;">{{ order.project | default("", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}<table width="100%">\n  <thead>\n    <tr>\n      <th style="width:86px;padding:6px 8px;">SO DATE</th><th style="width:82px;padding:6px 8px;">S/O NO</th><th style="width:92px;padding:6px 8px;">LOCATION</th><th style="width:140px;padding:6px 8px;">ITEM CODE</th>\n      <th style="width:56px;padding:6px 8px;">QTY</th>{% if cols.qty %}<th style="width:72px;padding:6px 8px;">QTY CHANGE TO</th>{% endif %}\n      <th style="width:92px;padding:6px 8px;">DELIVERY DATE</th>{% if cols.delivery_date %}<th style="width:92px;padding:6px 8px;">DELIVERY DATE CHANGE TO</th>{% endif %}\n      <th style="padding:6px 8px;">REMARK</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for line in handover.lines %}\n    <tr>\n      <td style="white-space:nowrap;padding:6px 8px;">{{ line.so_date | default("", true) }}</td>\n      <td style="white-space:nowrap;padding:6px 8px;">{{ line.so_number | default("", true) }}</td>\n      <td style="white-space:nowrap;padding:6px 8px;">{{ line.location | default("", true) }}</td>\n      <td style="white-space:nowrap;padding:6px 8px;">{{ line.item_code | default("", true) }}</td>\n      <td style="white-space:nowrap;padding:6px 8px;">{% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default("", true) }}{% endif %}</td>\n      {% if cols.qty %}<td style="white-space:nowrap;padding:6px 8px;">{% if line.was and line.was.qty %}{{ line.qty | default("", true) }}{% endif %}</td>{% endif %}\n      <td style="white-space:nowrap;padding:6px 8px;">{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default("", true) }}{% endif %}</td>\n      {% if cols.delivery_date %}<td style="white-space:nowrap;padding:6px 8px;">{% if line.was and line.was.delivery_date %}{{ line.delivery_date | default("", true) }}{% endif %}</td>{% endif %}\n      <td style="padding:6px 8px;">{{ line.remark | default("", true) }}{% if line.attachments %}<br>Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}<a href="{{ a.url }}">{{ a.name }}</a> (not attached, too large for email){% endif %}{% endfor %}{% endif %}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n<p>Raised by {{ actor.name if actor else \'-\' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>'

_PRIOR_BODY_TEXT = '{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}{{ handover.headline }}\n\n{% for order in handover.orders %}{{ order.so_number | default("", true) }}{% if order.customer %} - {{ order.customer }}{% endif %}{% if order.project %} - {{ order.project }}{% endif %}\n{% endfor %}\nSO DATE | S/O NO | LOCATION | ITEM CODE | QTY |{% if cols.qty %} QTY CHANGE TO |{% endif %} DELIVERY DATE |{% if cols.delivery_date %} DELIVERY DATE CHANGE TO |{% endif %} REMARK\n{% for line in handover.lines %}{{ line.so_date | default("", true) }} | {{ line.so_number | default("", true) }} | {{ line.location | default("", true) }} | {{ line.item_code | default("", true) }} | {% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default("", true) }}{% endif %} |{% if cols.qty %} {% if line.was and line.was.qty %}{{ line.qty | default("", true) }}{% endif %} |{% endif %} {% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default("", true) }}{% endif %} |{% if cols.delivery_date %} {% if line.was and line.was.delivery_date %}{{ line.delivery_date | default("", true) }}{% endif %} |{% endif %} {{ line.remark | default("", true) }}{% if line.attachments %} / Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}{{ a.name }} ({{ a.url }}) (not attached, too large for email){% endif %}{% endfor %}{% endif %}\n{% endfor %}\nRaised by {{ actor.name if actor else \'-\' }} on {{ today }}.\nOpen: {{ handover.link }}\n'


#: The row as it stood BEFORE `oihr_0004` (`eml_0002_seed_layouts`' document, no width
#: key), byte for byte off oihr_0004's own frozen copy. Crew hand test, 9 Oct: the dev
#: database still held this document (oihr_0004's guarded update had matched nothing
#: there), so a guard that only knew oihr_0004's OUTPUT updated 0 rows in silence. Both
#: unedited states go straight to the print document; oihr_0004 never wrote `body_text`,
#: so the text part is the same `_PRIOR_BODY_TEXT` in both.
_PRE_0004_LAYOUT = json.loads(r'''{
 "blocks": [
  {
   "type": "brand_header"
  },
  {
   "text": "{{ handover.headline }}",
   "type": "heading"
  },
  {
   "html": "<table width=\"100%\">\n  <thead>\n    <tr><th>S/O NO</th><th>CUSTOMER</th><th>PROJECT</th></tr>\n  </thead>\n  <tbody>\n    {% for order in handover.orders %}\n    <tr>\n      <td>{{ order.so_number | default(\"\", true) }}</td>\n      <td>{{ order.customer | default(\"\", true) }}</td>\n      <td>{{ order.project | default(\"\", true) }}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n{%- set cols = namespace(qty=false, delivery_date=false) -%}{%- for line in handover.lines -%}{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}{%- endfor -%}<table width=\"100%\">\n  <thead>\n    <tr>\n      <th>SO DATE</th><th>S/O NO</th><th>LOCATION</th><th>ITEM CODE</th>\n      <th>QTY</th>{% if cols.qty %}<th>QTY CHANGE TO</th>{% endif %}\n      <th>DELIVERY DATE</th>{% if cols.delivery_date %}<th>DELIVERY DATE CHANGE TO</th>{% endif %}\n      <th>REMARK</th>\n    </tr>\n  </thead>\n  <tbody>\n    {% for line in handover.lines %}\n    <tr>\n      <td>{{ line.so_date | default(\"\", true) }}</td>\n      <td>{{ line.so_number | default(\"\", true) }}</td>\n      <td>{{ line.location | default(\"\", true) }}</td>\n      <td>{{ line.item_code | default(\"\", true) }}</td>\n      <td>{% if line.was and line.was.qty %}{{ line.was.qty }}{% else %}{{ line.qty | default(\"\", true) }}{% endif %}</td>\n      {% if cols.qty %}<td>{% if line.was and line.was.qty %}{{ line.qty | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td>{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>\n      {% if cols.delivery_date %}<td>{% if line.was and line.was.delivery_date %}{{ line.delivery_date | default(\"\", true) }}{% endif %}</td>{% endif %}\n      <td>{{ line.remark | default(\"\", true) }}{% if line.attachments %}<br>Attachments: {% for a in line.attachments %}{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}{% else %}<a href=\"{{ a.url }}\">{{ a.name }}</a> (not attached, too large for email){% endif %}{% endfor %}{% endif %}</td>\n    </tr>\n    {% endfor %}\n  </tbody>\n</table>\n<p>Raised by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }}){% endif %} on {{ today }}.</p>",
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
 "version": 1
}''')

#: Every unedited (layout, text) state this migration upgrades: the pre-0004 document as
#: eml_0002 wrote it, the same document as the block editor re-saves it once
#: `EmailDocument.width` exists (`"width": "standard"` added, nothing else), and
#: oihr_0004's output.
_UNEDITED_STATES = [
    (_PRE_0004_LAYOUT, _PRIOR_BODY_TEXT),
    ({**_PRE_0004_LAYOUT, "width": "standard"}, _PRIOR_BODY_TEXT),
    (_PRIOR_LAYOUT, _PRIOR_BODY_TEXT),
]


def _set(
    bind, *, layout: dict, body_html: str, body_text: str, only_when: list, direction: str
) -> None:
    """Update the row only while its document AND its plain-text part are still one of
    `only_when`'s (layout, text) pairs (review N1: this migration also rewrites
    `body_text`, which oihr_0004 never did, so an admin edit to the text part alone must
    stop it too); log and leave any other row alone. Insert when no row exists."""
    existing = bind.execute(
        sa.text("SELECT id FROM email_templates WHERE code = :code"),
        {"code": TEMPLATE_CODE},
    ).first()
    if existing:
        touched = 0
        for candidate_layout, candidate_text in only_when:
            touched += bind.execute(
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
                    "only_when": json.dumps(candidate_layout),
                    "only_when_text": candidate_text,
                },
            ).rowcount
            if touched:
                break
        if not touched:
            logger.warning(
                "oihr_0005 %s: email_templates row %s carries a document this migration "
                "did not write (admin-edited); left untouched.",
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
                "the SO date, line table (stock location, item, QTY / NEW QTY, DELIVERY / "
                "NEW DATE, remark), on the print shell that fits one A4 portrait page."
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
        layout=_PRINT_LAYOUT,
        body_html=_PRINT_BODY_HTML,
        body_text=_PRINT_BODY_TEXT,
        only_when=_UNEDITED_STATES,
        direction="upgrade",
    )


def downgrade() -> None:
    _set(
        op.get_bind(),
        layout=_PRIOR_LAYOUT,
        body_html=_PRIOR_BODY_HTML,
        body_text=_PRIOR_BODY_TEXT,
        only_when=[(_PRINT_LAYOUT, _PRINT_BODY_TEXT)],
        direction="downgrade",
    )
