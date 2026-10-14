"""Order inquiry handover email back to its earlier shape, with a REMARK column (ORDERBACK-DELTA
owner hand test, 14 Oct 2026, `documentation/plans/scm/PLAN-orderback-delta-13oct.md`).

Owner: "just like before". The S/O facts (S/O NO, SO DATE, CUSTOMER, PROJECT) print once in
their own table at the top, the item lines below: LOCATION | ITEM CODE | [NEW ITEM CODE] | QTY
| [NEW QTY] | DELIVERY | [NEW DATE] | REMARK. That is `oihr_0006`'s document word for word, with
the REMARK column added last (each saved line's decision) and one plain heading, ORDER INQUIRY,
in place of the per-case headline: the details live only in each line's REMARK.

Updates ``email_templates`` (``code`` = ``TEMPLATE_CODE``) IN PLACE only while its
``layout_json`` and ``body_text`` are still exactly what `oihr_0007_excel_remark` wrote; a row
an admin edited is left alone with a logged warning. A database with no row gets one inserted.
Idempotent. Downgrade restores `oihr_0007`'s document, read from that migration's own frozen
constants.

Revision ID: oihr_0008_so_table_on_top
Revises: oihr_0007_excel_remark
Create Date: 2026-10-14
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

from alembic import op


revision = "oihr_0008_so_table_on_top"
down_revision = "oihr_0007_excel_remark"
branch_labels = None
depends_on = None


def _load(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"{name}_frozen", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BASE = _load("oihr_0006_no_remark_column")
_PREVIOUS = _load("oihr_0007_excel_remark")

HEADING = "ORDER INQUIRY"


def _swap(text: str, old: str, new: str) -> str:
    """One exact edit of the frozen `oihr_0006` text; fails loudly if that text moved."""
    if text.count(old) != 1:
        raise RuntimeError(f"oihr_0008: expected one {old[:60]!r} in oihr_0006's document")
    return text.replace(old, new)


def _html() -> str:
    html = _BASE._LAYOUT["blocks"][2]["html"]
    html = _swap(
        html,
        "{%- set span = 4 + ",
        "{%- set span = 5 + ",
    )
    # Widths: oihr_0006's weights plus REMARK (30), which takes what is left of 100.
    html = _swap(
        html,
        "{%- set total = 16 + 26 + 10 + 15 + ",
        "{%- set total = 16 + 26 + 10 + 15 + 30 + ",
    )
    html = _swap(
        html,
        "{%- if cols.delivery_date %}{% set w.dd = (15 * 100) // total %}{% set w.new_dd = "
        "100 - w.loc - w.code - w.new_code - w.qty - w.new_qty - w.dd %}{% else %}"
        "{% set w.dd = 100 - w.loc - w.code - w.new_code - w.qty - w.new_qty %}{% endif -%}",
        "{%- set w.dd = (15 * 100) // total -%}"
        "{%- if cols.delivery_date %}{% set w.new_dd = (15 * 100) // total %}{% endif -%}"
        "{%- set rem = 100 - w.loc - w.code - w.new_code - w.qty - w.new_qty - w.dd - w.new_dd -%}",
    )
    html = _swap(
        html,
        'NEW DATE</th>{% endif %}\n    </tr>',
        'NEW DATE</th>{% endif %}'
        '<th width="{{ rem }}%" style="width:{{ rem }}%;padding:4px;">REMARK</th>\n    </tr>',
    )
    html = _swap(
        html,
        '{{ line.delivery_date | default("", true) }}{% endif %}</td>{% endif %}\n    </tr>',
        '{{ line.delivery_date | default("", true) }}{% endif %}</td>{% endif %}\n'
        '      <td style="padding:4px;">{{ line.remark | default("", true) }}</td>\n    </tr>',
    )
    return html


def _layout() -> dict:
    layout = copy.deepcopy(_BASE._LAYOUT)
    blocks = layout["blocks"]
    if blocks[1] != {"text": "{{ handover.headline }}", "type": "heading"}:
        raise RuntimeError("oihr_0008: oihr_0006's heading block moved")
    blocks[1] = {"text": HEADING, "type": "heading"}
    blocks[2]["html"] = _html()
    return layout


def _body_text() -> str:
    text = _BASE._BODY_TEXT
    text = _swap(text, "{{ handover.headline }}\n\n", f"{HEADING}\n\n")
    text = _swap(
        text,
        " DELIVERY{% if cols.delivery_date %} | NEW DATE{% endif %}\n",
        " DELIVERY{% if cols.delivery_date %} | NEW DATE{% endif %} | REMARK\n",
    )
    text = _swap(
        text,
        '{{ line.delivery_date | default("", true) }}{% endif %}{% endif %}\n{% endfor %}',
        '{{ line.delivery_date | default("", true) }}{% endif %}{% endif %}'
        ' | {{ line.remark | default("", true) }}\n{% endfor %}',
    )
    return text


_LAYOUT = _layout()
_BODY_TEXT = _body_text()


def upgrade() -> None:
    _BASE._set(
        op.get_bind(),
        layout=_LAYOUT,
        body_html=_BASE._mirror(_LAYOUT),
        body_text=_BODY_TEXT,
        only_when=(_PREVIOUS._LAYOUT, _PREVIOUS._BODY_TEXT),
        direction="upgrade",
    )


def downgrade() -> None:
    _BASE._set(
        op.get_bind(),
        layout=_PREVIOUS._LAYOUT,
        body_html=_BASE._mirror(_PREVIOUS._LAYOUT),
        body_text=_PREVIOUS._BODY_TEXT,
        only_when=(_LAYOUT, _BODY_TEXT),
        direction="downgrade",
    )
