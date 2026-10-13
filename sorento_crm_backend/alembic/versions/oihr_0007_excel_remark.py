"""Order inquiry handover email in the owner's Excel "ORDER INQUIRY" layout, REMARK back
(ORDERBACK-DELTA extension, owner 13 Oct 2026 22:40,
`documentation/plans/scm/PLAN-orderback-delta-13oct.md`).

One line per SAVED line of a Confirm, its REMARK saying what was decided (ORDER, USE BRW,
USE <own location>, ORDER BACK, an SPO number, CANCEL, LOCAL BUY), in the sheet's columns:
SO DATE | S/O NO | ITEM CODE | QTY | DELIVERY DATE | PROJECT/CUSTOMER | STOCK LOCATION |
REMARK. The change columns `oihr_0006` introduced stay, shown only when some line moved:
NEW ITEM CODE after ITEM CODE, QTY CHANGE TO after QTY, DELIVERY DATE CHANGE TO after
DELIVERY DATE. The S/O summary table goes: every line now carries its own S/O and
project/customer, as the sheet does. Headline, attachments line, button and print shell are
`oihr_0006`'s.

Updates ``email_templates`` (``code`` = ``TEMPLATE_CODE``) IN PLACE only while its
``layout_json`` and ``body_text`` are still exactly what `oihr_0006_no_remark_column` wrote; a
row an admin edited is left alone with a logged warning. A database with no row gets one
inserted. Idempotent. Downgrade restores `oihr_0006`'s document, read from that migration's own
frozen constants.

Revision ID: oihr_0007_excel_remark
Revises: spec_in_0001_stage_label
Create Date: 2026-10-13
"""
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op


logger = logging.getLogger("alembic.runtime.migration")

revision = "oihr_0007_excel_remark"
down_revision = "spec_in_0001_stage_label"
branch_labels = None
depends_on = None


def _previous():
    """`oihr_0006`'s module, loaded from its file: its `_LAYOUT` / `_BODY_HTML` /
    `_BODY_TEXT` are the frozen document this upgrade converts and downgrade restores, and
    its `_set` / `TEMPLATE_CODE` / `_SUBJECT` / `_mirror` are reused rather than restated."""
    path = Path(__file__).with_name("oihr_0006_no_remark_column.py")
    spec = importlib.util.spec_from_file_location("oihr_0006_frozen", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CELL = "white-space:nowrap;padding:4px;"
_TH = "white-space:nowrap;padding:4px;"

_TABLE = (
    "{%- set cols = namespace(item_code=false, qty=false, delivery_date=false) -%}"
    "{%- for line in handover.lines -%}"
    "{%- if line.was and line.was.item_code %}{% set cols.item_code = true %}{% endif -%}"
    "{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}"
    "{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}"
    "{%- endfor -%}\n"
    '<table width="100%" style="font-size:11px;line-height:15px;">\n'
    '  <thead style="display:table-header-group;">\n'
    "    <tr>\n"
    f'      <th style="{_TH}">SO DATE</th><th style="{_TH}">S/O NO</th>'
    f'<th style="{_TH}">ITEM CODE</th>'
    f'{{% if cols.item_code %}}<th style="{_TH}">NEW ITEM CODE</th>{{% endif %}}'
    f'<th style="{_TH}">QTY</th>'
    f'{{% if cols.qty %}}<th style="{_TH}">QTY CHANGE TO</th>{{% endif %}}'
    f'<th style="{_TH}">DELIVERY DATE</th>'
    f'{{% if cols.delivery_date %}}<th style="{_TH}">DELIVERY DATE CHANGE TO</th>{{% endif %}}'
    f'<th style="padding:4px;">PROJECT/CUSTOMER</th><th style="{_TH}">STOCK LOCATION</th>'
    f'<th style="padding:4px;">REMARK</th>\n'
    "    </tr>\n"
    "  </thead>\n"
    "  <tbody>\n"
    "    {% for line in handover.lines %}\n"
    '    <tr style="page-break-inside:avoid;">\n'
    f'      <td style="{_CELL}">{{{{ line.so_date | default("", true) }}}}</td>\n'
    f'      <td style="{_CELL}">{{{{ line.so_number | default("", true) }}}}</td>\n'
    f'      <td style="{_CELL}">{{% if line.was and line.was.item_code %}}{{{{ line.was.item_code }}}}'
    '{% else %}{{ line.item_code | default("", true) }}{% endif %}</td>\n'
    f'      {{% if cols.item_code %}}<td style="{_CELL}">{{% if line.was and line.was.item_code %}}'
    '{{ line.item_code | default("", true) }}{% endif %}</td>{% endif %}\n'
    f'      <td style="{_CELL}">{{% if line.was and line.was.qty %}}{{{{ line.was.qty }}}}'
    '{% else %}{{ line.qty | default("", true) }}{% endif %}</td>\n'
    f'      {{% if cols.qty %}}<td style="{_CELL}">{{% if line.was and line.was.qty %}}'
    '{{ line.qty | default("", true) }}{% endif %}</td>{% endif %}\n'
    f'      <td style="{_CELL}">{{% if line.was and line.was.delivery_date %}}'
    '{{ line.was.delivery_date }}{% else %}{{ line.delivery_date | default("", true) }}{% endif %}</td>\n'
    f'      {{% if cols.delivery_date %}}<td style="{_CELL}">{{% if line.was and line.was.delivery_date %}}'
    '{{ line.delivery_date | default("", true) }}{% endif %}</td>{% endif %}\n'
    '      <td style="padding:4px;">{{ [line.customer, line.project] | select | join(" / ") }}</td>\n'
    f'      <td style="{_CELL}">{{{{ line.location | default("", true) }}}}</td>\n'
    '      <td style="padding:4px;">{{ line.remark | default("", true) }}</td>\n'
    "    </tr>\n"
    "    {% endfor %}\n"
    "  </tbody>\n"
    "</table>\n"
)

_ATTACHMENTS_AND_FOOTER = (
    '{%- set att = handover.lines | selectattr("attachments") | list -%}\n'
    '{% if att %}<p style="font-size:11px;">Attachments: {% for line in att %}'
    "{% if not loop.first %}; {% endif %}{{ line.item_code | default(\"\", true) }}: "
    "{% for a in line.attachments %}{% if not loop.first %}, {% endif %}"
    '{% if a.attached %}{{ a.name }}{% else %}<a href="{{ a.url }}">{{ a.name }}</a> '
    "(not attached, too large for email){% endif %}{% endfor %}{% endfor %}</p>{% endif %}\n"
    "<p>Raised by {{ actor.name if actor else '-' }}{% if actor %} ({{ actor.email }})"
    "{% endif %} on {{ today }}.</p>"
)

_LAYOUT = {
    "blocks": [
        {"type": "brand_header"},
        {"text": "{{ handover.headline }}", "type": "heading"},
        {"html": _TABLE + _ATTACHMENTS_AND_FOOTER, "type": "custom_text"},
        {"url": "{{ handover.link }}", "type": "button", "label": "Open in Order Inquiries"},
        {"type": "footer"},
    ],
    "version": 1,
    "width": "print",
}

_BODY_TEXT = (
    "{%- set cols = namespace(item_code=false, qty=false, delivery_date=false) -%}"
    "{%- for line in handover.lines -%}"
    "{%- if line.was and line.was.item_code %}{% set cols.item_code = true %}{% endif -%}"
    "{%- if line.was and line.was.qty %}{% set cols.qty = true %}{% endif -%}"
    "{%- if line.was and line.was.delivery_date %}{% set cols.delivery_date = true %}{% endif -%}"
    "{%- endfor -%}{{ handover.headline }}\n\n"
    "SO DATE | S/O NO | ITEM CODE |{% if cols.item_code %} NEW ITEM CODE |{% endif %} QTY |"
    "{% if cols.qty %} QTY CHANGE TO |{% endif %} DELIVERY DATE |"
    "{% if cols.delivery_date %} DELIVERY DATE CHANGE TO |{% endif %}"
    " PROJECT/CUSTOMER | STOCK LOCATION | REMARK\n"
    "{% for line in handover.lines %}"
    '{{ line.so_date | default("", true) }} | {{ line.so_number | default("", true) }} | '
    "{% if line.was and line.was.item_code %}{{ line.was.item_code }}"
    '{% else %}{{ line.item_code | default("", true) }}{% endif %} |'
    "{% if cols.item_code %} {% if line.was and line.was.item_code %}"
    '{{ line.item_code | default("", true) }}{% endif %} |{% endif %} '
    "{% if line.was and line.was.qty %}{{ line.was.qty }}"
    '{% else %}{{ line.qty | default("", true) }}{% endif %} |'
    "{% if cols.qty %} {% if line.was and line.was.qty %}"
    '{{ line.qty | default("", true) }}{% endif %} |{% endif %} '
    "{% if line.was and line.was.delivery_date %}{{ line.was.delivery_date }}"
    '{% else %}{{ line.delivery_date | default("", true) }}{% endif %} |'
    "{% if cols.delivery_date %} {% if line.was and line.was.delivery_date %}"
    '{{ line.delivery_date | default("", true) }}{% endif %} |{% endif %} '
    '{{ [line.customer, line.project] | select | join(" / ") }} | '
    '{{ line.location | default("", true) }} | {{ line.remark | default("", true) }}\n'
    "{% endfor %}"
    '{%- set att = handover.lines | selectattr("attachments") | list -%}{% if att %}\n'
    "Attachments: {% for line in att %}{% if not loop.first %}; {% endif %}"
    '{{ line.item_code | default("", true) }}: {% for a in line.attachments %}'
    "{% if not loop.first %}, {% endif %}{% if a.attached %}{{ a.name }}"
    "{% else %}{{ a.name }} ({{ a.url }}) (not attached, too large for email){% endif %}"
    "{% endfor %}{% endfor %}\n{% endif %}\n"
    "Raised by {{ actor.name if actor else '-' }} on {{ today }}.\n"
    "Open: {{ handover.link }}\n"
)


def upgrade() -> None:
    previous = _previous()
    previous._set(
        op.get_bind(),
        layout=_LAYOUT,
        body_html=previous._mirror(_LAYOUT),
        body_text=_BODY_TEXT,
        only_when=(previous._LAYOUT, previous._BODY_TEXT),
        direction="upgrade",
    )


def downgrade() -> None:
    previous = _previous()
    previous._set(
        op.get_bind(),
        layout=previous._LAYOUT,
        body_html=previous._BODY_HTML,
        body_text=previous._BODY_TEXT,
        only_when=(_LAYOUT, _BODY_TEXT),
        direction="downgrade",
    )
