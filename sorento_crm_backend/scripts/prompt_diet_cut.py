"""PROMPT-DIET: cut the v50 parser prompt into a stable core and per-area cards.

Move only: every v50 section lands, verbatim, in exactly one place (the core file or one card in
the cards file). Sections are assigned by heading TEXT from the table below, so the cut does not
depend on line numbers. The one addition is the `== AREAS ==` block in core, the list the
parser reads to name an area. `== DECISIVE DOMAIN TERMS ==` is kept verbatim at the end of
core this step (plan: removal is the trim round).

Run once, commit the output (documentation/plans/chatbot/PLAN-prompt-diet-11oct.md):
  venv/bin/python scripts/prompt_diet_cut.py --src alembic/data/chatbot_semantic_parser.v50.txt \
      --out-dir alembic/data
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^==+ .* ==+$")
CORE_FILE = "chatbot_parser_core.v1.txt"
CARDS_FILE = "chatbot_parser_cards.v1.txt"
DECISIVE = "== DECISIVE DOMAIN TERMS =="

#: Exactly the domain_hint values v50 allows (its INTENT & DOMAIN enum plus purchase_order from
#: its PURCHASE ORDERS section), one line each. No area is invented here.
AREAS_BLOCK = """== AREAS ==
Areas this bot answers (the domain_hint values):
master_products: product information, specifications, dimensions, brands and categories
product_attachment: a product's photos, certificates, technical drawings and installation guides
promotion: promotions and the flyers that go with them
forms: forms
inventory: stock on hand and low stock reports
order: customer orders, delivery status, outstanding orders, and the sales report, sales analysis, top selling and sales ranking asks
incoming: incoming shipments and containers, with their ETA
portal_link: a link to the complaint, stock inquiry, product inquiry, purchase request or sponsorship form
resource_attachment: shared documents and resources such as catalogues, warranty papers and tag templates
goods_receive: goods receive notes (GRN)
spo_allocation: the last receipt of a supplier purchase order (SPO)
ideate: an idea, feature request or improvement from the user
purchase_order: outstanding purchase orders
purchase_cost: what was last paid a supplier for a product
"""

#: Heading text -> destination. Generated from the plan's Cut table (v50 baseline).
CUT: dict[str, list[str]] = {
    "core": [
        '== PRICE TERMINOLOGY (Sorento-specific, decisive) ==',
        '== AFFIRMATION ==',
        '== READING THE CURRENT MESSAGE IN CONTEXT ==',
        '== INTENT & DOMAIN ==',
        '== DOMAIN IN MESSAGE ==',
        '== BARE ENTITY CONTINUATION ==',
        '== SCOPE INTENT ==',
        '== MESSAGE TYPE ==',
        '== USER GOAL ==',
        '== ENTITY OPERATIONS ==',
        '== WHAT IS NEVER AN ENTITY, AND WHAT ALWAYS IS ==',
        '== PRONOUN REFERENCE, NEVER AN ENTITY ==',
        '== ENTITY CONFIDENCE ==',
        '== MATCH MODE ==',
        '== DEMAND QTY ==',
        '== CORRECTION / DOUBT ==',
        '== ROUTING ==',
        '== DATE FILTER (per-message, never carried over) ==',
        '== POSITIONAL REFERENCES ==',
        '== REFERENCE TARGET (which set a positional reply means) ==',
        '== PERSON-NAME MENTION ==',
        '== ASKS (a message that names more than one domain) ==',
        '== TOPIC RESET ==',
        '== OUTPUT (exactly these keys, no others, no comments) ==',
        '== A NEW QUESTION IS NEVER A "YES" ==',
        '== A BARE MONTH NAME IS STILL A DATE FILTER, EVEN BESIDE A CHANNEL WORD AND A CODE ==',
        '== entities[].quantity ==',
        '== QUANTITY: entities[].quantity ==',
        '== KNOWN BRANDS: entities[].hint "brand" ==',
        '== A WORD BESIDE A CODE IS STILL ONE PRODUCT ENTITY ==',
        '== MEMORY ==',
    ],
    "open_question": [
        '== AN OPEN NUMBERED QUESTION IS ANSWERED WITH reference_positions ==',
        '== AN OPEN TASK IS A FACT, AND THE MESSAGE MAY BE ABOUT IT ==',
        '== A LINE BEGINNING "Last answered:" ==',
        '== THE OPEN QUESTION AND open_question_answer ==',
    ],
    "escalation": [
        '== COMPANY-NAME REPLY ON AN ESCALATION OFFER ==',
    ],
    "account": [
        '== SELF_REFERENCE: the asker means their OWN account ==',
        '== ACCOUNT: which numbered account of a customer the message names ==',
    ],
    "master_products": [
        '== REQUESTED ATTRIBUTES ==',
        '== BROADEN AXIS ==',
        '== A BARE "SPEC" ASKS FOR ALL OF THEM ==',
        '== THE ATTRIBUTE PHRASE, WHOLE ==',
        '== THE specification ENTITY ==',
    ],
    "sales": [
        '== SALES REPORT: order_status "sales_report", domain_hint "order" ==',
        '== SALES_CHANNEL: a NEW nullable output key, "dealer" | "project" | null ==',
        '== A NARROWING REPLY UNDER AN OPEN SALES REPORT / OUTSTANDING DETAIL OFFER ==',
        '== SALES ANALYSIS: order_status "sales_analysis", domain_hint "order" ==',
        '== DATES ON A SALES ANALYSIS ==',
        '== SALES_BASIS: "ordered" | "delivered" | "invoiced" | null ==',
        '== SALES_COMPANY: the company named, or null ==',
        '== TOP SELLING: order_status "top_selling", domain_hint "order" ==',
        '== TOP_N on a top selling ask ==',
        '== RANK_BY: what the ranking is by ==',
        '== BASIS: which sale is counted ==',
        '== RANK_GROUP: what is ranked ==',
        "== ANSWERING THE BOT'S OWN TOP SELLING QUESTION ==",
        '== ANSWERING "CUSTOMER OR SALES AGENT?" ==',
        '== RANK_DIRECTION: most sold first or least sold first ==',
        '== NARROWING A TOP SELLING ASK ==',
        '== FROM A RANKING TO ANOTHER ORDER REPORT ==',
        '== SALES RANKING: order_status "sales_ranking", domain_hint "order" ==',
    ],
    "order": [
        '== ORDER_STATUS FILTER ==',
        '== GROUP_BY ==',
        '== TOP_N ==',
        '== ORDER_STATUS GAINS "so_outstanding" ==',
        '== ORDER_STATUS GAINS "do_outstanding" AND "outstanding_both" ==',
        '== IN AN ORDER OR OUTSTANDING ASK, A SHORT TOKEN IS A LOCATION ==',
        '== A DELIVERY WORD PLUS A NAME IS AN ORDER ASK ==',
        '== SORT_BY AND SORT_DIR: the order the customer asked for ==',
        '== A SORT WORD ALONE RE-SORTS THE LIST ON SCREEN ==',
    ],
    "promotion": [
        '== FLYER FLAG ==',
        '== ACCESS LEVELS ==',
        '== IS_ACTIVE FILTER ==',
        '== PROMOTION KIND ==',
        '== PROMOTION SUBJECT ==',
    ],
    "resource_attachment": [
        '== DOCUMENT ==',
    ],
    "purchase_order": [
        '== PURCHASE ORDERS: domain_hint "purchase_order", intent_hint "check_po" ==',
        '== IN A PO ASK, THE CODE IS THE PRODUCT ==',
        '== UNDER A PO OR SPO ASK, A SHORT TOKEN IS A LOCATION TOO ==',
    ],
    "spo_allocation": [
        '== SPO LAST RECEIPT: intent_hint "check_spo", domain_hint "spo_allocation" ==',
        '== THE WORD SPO IS AN SPO ALLOCATION ASK: domain_hint "spo_allocation", intent_hint "check_spo" ==',
    ],
    "purchase_cost": [
        '== LAST PURCHASE COST: intent_hint "check_po_cost", domain_hint "purchase_cost" ==',
    ],
    "inventory": [
        '== LOW STOCK REPORT: intent_hint "low_stock_report", domain_hint "inventory" ==',
    ],
    "ideate": [
        '== IDEATION CONTINUATION ==',
    ],
    "product_attachment": [
        '== ATTACHMENT TYPE EXTRACTION (product_attachment) ==',
    ],
}


def split_chunks(text: str) -> list[tuple[str, str]]:
    """(heading or "", chunk text): the preamble first, then one chunk per heading line."""
    chunks: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if HEADING.match(line):
            chunks.append((line, [line]))
        else:
            chunks[-1][1].append(line)
    return [(h, "\n".join(lines).rstrip("\n")) for h, lines in chunks if "".join(lines).strip()]


def build(text: str) -> tuple[str, str]:
    chunks = split_chunks(text)
    owner: dict[str, str] = {}
    for dest, headings in CUT.items():
        for heading in headings:
            assert heading not in owner, f"heading assigned twice: {heading}"
            owner[heading] = dest
    by_heading = {h: body for h, body in chunks if h}
    assert len(by_heading) == len([h for h, _ in chunks if h]), "duplicate heading in source"
    unassigned = [h for h in by_heading if h not in owner and h != DECISIVE]
    unknown = [h for h in owner if h not in by_heading]
    assert not unassigned and not unknown, f"unassigned: {unassigned}; not in source: {unknown}"

    core: list[str] = []
    cards: dict[str, list[str]] = {}
    for heading, body in chunks:
        if heading == DECISIVE:
            continue
        dest = "core" if not heading else owner[heading]
        (core if dest == "core" else cards.setdefault(dest, [])).append(body)
    core.append(AREAS_BLOCK.rstrip("\n"))
    core.append(by_heading[DECISIVE])

    cards_text = "".join(
        f"<<card:{name}>>\n" + "\n\n".join(parts) + "\n\n" for name, parts in cards.items()
    )
    return "\n\n".join(core) + "\n", cards_text.rstrip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()
    core, cards = build(args.src.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / CORE_FILE).write_text(core, encoding="utf-8")
    (args.out_dir / CARDS_FILE).write_text(cards, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
