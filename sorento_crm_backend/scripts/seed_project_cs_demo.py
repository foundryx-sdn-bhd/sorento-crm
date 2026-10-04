"""Seed the Tuju Residences demo so the lead-to-SO journey can be walked end to end.

The client's own documents are the acceptance test, but a document has nothing to
reconcile against on an empty database. Uploading their purchase order without the
quotation it was priced from produces a draft that cannot price a single line, which
looks like a broken feature rather than a missing prerequisite.

So this script lays down exactly the prerequisites and nothing more:

* the parties and the buyer, resolved to the REAL customer rows already in this
  database (`BUIMACO SDN BHD (PROJECT)`, `SLG CONSTRUCTION SDN BHD (PROJECT)`), because
  a client is a customers row that gets reused, never a duplicate;
* the project `TUJU RESIDENCE AT J`, carrying the filing reference `PS26-0143` that
  project sales admin writes on every piece of paper for this job;
* quotation `QT-004188` with all 60 of its lines, priced parents and their zero-priced
  companions, which is what makes set explosion and the PO cross-check possible;
* two leads, one waiting to be accepted and one not yet assigned, so the acceptance
  handshake has something to act on without pre-empting the walk-through.

The quotation lines are the real ones, transcribed from the committed PDF and verified
by arithmetic: every line satisfies quantity times unit price equals total, and the
sixty lines sum to exactly 1,805,907.02, which is the printed grand total. Six lines
resolve to no product in this catalogue (a connector and a bottle trap that are not in
the item master, one truncated description, and a code that exists only with a suffix).
Those are left unresolved ON PURPOSE: they are the real state of the catalogue, and the
sales order draft should raise them rather than have them quietly seeded away.

Idempotent. Re-running finds what exists by natural key and leaves it alone. The
`--reset` path deletes ONLY the rows this script creates, found by those same keys;
there is no unscoped delete anywhere in here, because this database is a copy of
production.

    venv/bin/python scripts/seed_project_cs_demo.py           # create or top up
    venv/bin/python scripts/seed_project_cs_demo.py --reset   # remove, then create
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.order import Customer  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.projects import (  # noqa: E402
    Project,
    ProjectLead,
    ProjectParty,
    ProjectQuotation,
    ProjectQuotationLine,
    ProjectQuotationVersion,
)
from app.services.company_scope import set_company_scope  # noqa: E402

SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

PROJECT_TITLE = "TUJU RESIDENCE AT J"
PROJECT_ADMIN_REF = "PS26-0143"
QUOTATION_SCOPE = "QT-004188 Tuju Residences"
BUYER_NAME = "BUIMACO SDN BHD (PROJECT)"
ISSUER_NAME = "SLG CONSTRUCTION SDN BHD (PROJECT)"

LEAD_WAITING_TITLE = "Residensi Bukit Raja Phase 2"
LEAD_UNASSIGNED_TITLE = "Kepong Metropolitan Serviced Apartments"

LINES_FILE = Path(__file__).resolve().parents[2] / (
    "sorento_crm_frontend/e2e/fixtures/project-cs/quotation-qt-004188.lines.json"
)


# --------------------------------------------------------------------------- helpers


def _actor(db: Session) -> str:
    """Whoever will be walking the demo. Overridable, because the owner shown on the
    project should be a person the user recognises."""
    override = os.environ.get("SEED_ACTOR_USER_ID")
    if override:
        return override
    row = db.execute(
        sa.text(
            "SELECT id FROM users WHERE status = 'ACTIVE' AND email = :email LIMIT 1"
        ),
        {"email": "person38@example.com"},
    ).first()
    if row:
        return str(row[0])
    row = db.execute(
        sa.text("SELECT id FROM users WHERE status = 'ACTIVE' ORDER BY created_at LIMIT 1")
    ).first()
    if not row:
        raise SystemExit("No active user to own the demo project.")
    return str(row[0])


def _customer(db: Session, name: str) -> Customer:
    """Resolve to an EXISTING customer. This database has duplicate Buimaco rows under
    one code, so pick deterministically by oldest rather than whichever comes back
    first, or the demo points at a different row on each machine."""
    row = (
        db.query(Customer)
        .filter(Customer.customer_name == name)
        .order_by(Customer.created_at.asc(), Customer.id.asc())
        .first()
    )
    if not row:
        raise SystemExit(f"Customer '{name}' is not in this database; nothing to point at.")
    return row


def _party(db: Session, *, party_type: str, name: str, customer: Customer | None,
           actor: str) -> ProjectParty:
    existing = (
        db.query(ProjectParty)
        .filter(ProjectParty.party_type == party_type, ProjectParty.name == name)
        .first()
    )
    if existing:
        return existing
    party = ProjectParty(
        company_id=SORENTO_COMPANY_ID,
        party_type=party_type,
        name=name,
        customer_id=str(customer.id) if customer else None,
        is_active=True,
        created_by=actor,
    )
    db.add(party)
    db.flush()
    return party


def _quotation_lines() -> list[dict]:
    if not LINES_FILE.exists():
        raise SystemExit(
            f"Quotation lines fixture missing: {LINES_FILE}\n"
            "It is committed alongside the PDF it was transcribed from."
        )
    payload = json.loads(LINES_FILE.read_text())
    lines = payload["lines"]
    # The fixture is checkable, so check it rather than trusting it. A silently edited
    # fixture would seed a quotation the client's PO cannot reconcile against, and the
    # symptom would show up three screens later as a pricing bug.
    total = Decimal("0")
    for line in lines:
        qty = Decimal(str(line["qty"]))
        price = Decimal(str(line["unit_price"]))
        expected = (qty * price).quantize(Decimal("0.01"), ROUND_HALF_UP)
        stated = Decimal(str(line["total"])).quantize(Decimal("0.01"))
        if expected != stated:
            raise SystemExit(
                f"Quotation fixture line {line['item']} does not multiply out: "
                f"{qty} x {price} = {expected}, fixture says {stated}"
            )
        total += stated
    if total != Decimal(str(payload["grand_total"])):
        raise SystemExit(
            f"Quotation fixture lines sum to {total}, printed grand total is "
            f"{payload['grand_total']}"
        )
    return lines


# --------------------------------------------------------------------------- seeding


def seed(db: Session) -> dict:
    actor = _actor(db)
    buyer = _customer(db, BUYER_NAME)
    issuer = _customer(db, ISSUER_NAME)

    developer = _party(db, party_type="developer", name="TUJU DEVELOPMENT SDN BHD",
                       customer=None, actor=actor)
    contractor = _party(db, party_type="main_contractor", name=BUYER_NAME,
                        customer=buyer, actor=actor)
    _party(db, party_type="main_contractor", name=ISSUER_NAME, customer=issuer, actor=actor)
    _party(db, party_type="architect", name="ARKITEK MAA (M) SDN BHD", customer=None,
           actor=actor)

    project = (
        db.query(Project)
        .filter(Project.title == PROJECT_TITLE)
        .order_by(Project.created_at.asc())
        .first()
    )
    created_project = False
    if not project:
        from app.services import project_service

        project = project_service.register_project(
            db,
            company_id=SORENTO_COMPANY_ID,
            actor_user_id=actor,
            developer_party_id=str(developer.id),
            title=PROJECT_TITLE,
            details={
                "location": "No. 1, Jalan PJU 1A/41B, Ara Jaya, 47301 Petaling Jaya, Selangor",
                "main_contractor_party_id": str(contractor.id),
            },
            owner_user_id=actor,
        )
        created_project = True
    if not project.admin_ref:
        project.admin_ref = PROJECT_ADMIN_REF
    db.flush()

    quotation = (
        db.query(ProjectQuotation)
        .filter(
            ProjectQuotation.project_id == project.id,
            ProjectQuotation.scope_label == QUOTATION_SCOPE,
        )
        .first()
    )
    created_lines = 0
    if not quotation:
        from app.services import project_quotation_service as quotes

        quotation = quotes.create_quotation(
            db,
            project=project,
            actor_user_id=actor,
            payload={
                "scope_label": QUOTATION_SCOPE,
                "notes": (
                    "Transcribed from the client's own quotation QT-004188 dated "
                    "28/01/2026. Sixty lines, 52 priced parents and 8 zero-priced "
                    "companions, summing to 1,805,907.02."
                ),
            },
        )
        version = (
            db.query(ProjectQuotationVersion)
            .filter(ProjectQuotationVersion.quotation_id == quotation.id)
            .order_by(ProjectQuotationVersion.version_no.desc())
            .first()
        )

        codes = {}
        for line in _quotation_lines():
            code = (line.get("code") or "").strip()
            if code and code.upper() not in codes:
                product = (
                    db.query(Product)
                    .filter(sa.func.upper(Product.product_code) == code.upper())
                    .first()
                )
                codes[code.upper()] = product

        total = Decimal("0")
        for line in _quotation_lines():
            code = (line.get("code") or "").strip()
            product = codes.get(code.upper()) if code else None
            qty = Decimal(str(line["qty"]))
            price = Decimal(str(line["unit_price"]))
            line_total = Decimal(str(line["total"]))
            db.add(
                ProjectQuotationLine(
                    company_id=SORENTO_COMPANY_ID,
                    version_id=version.id,
                    product_id=str(product.id) if product else None,
                    # The snapshot carries the code even when the catalogue does not
                    # have it. That unresolved line is the real state of the item
                    # master and the sales order draft should say so, not inherit a
                    # silently invented product.
                    product_code_snapshot=code or None,
                    description_snapshot=line["description"],
                    unit_price=price,
                    quantity=qty,
                    uom=line.get("uom"),
                    line_total=line_total,
                    sort_order=int(line["item"]),
                )
            )
            total += line_total
            created_lines += 1
        version.total_amount = total
        db.flush()

    leads = _seed_leads(db, actor=actor, buyer=buyer, developer=developer)
    db.commit()

    return {
        "project": f"{project.project_code} {project.title}",
        "project_created": created_project,
        "admin_ref": project.admin_ref,
        "quotation": quotation.scope_label,
        "quotation_lines_created": created_lines,
        "leads": leads,
        "owner_user_id": actor,
    }


def _seed_leads(db: Session, *, actor: str, buyer: Customer,
                developer: ProjectParty) -> dict:
    """One lead waiting to be accepted and one not yet assigned.

    Two rather than one because the acceptance handshake only reads as a handshake when
    you can see both sides of it: something sitting on a salesperson's desk, and
    something still in the pool.
    """
    from app.services import project_lead_service as leads_service

    out = {}
    waiting = (
        db.query(ProjectLead).filter(ProjectLead.title == LEAD_WAITING_TITLE).first()
    )
    if not waiting:
        waiting = leads_service.create_lead(
            db,
            company_id=SORENTO_COMPANY_ID,
            actor_user_id=actor,
            payload={
                "title": LEAD_WAITING_TITLE,
                "customer_id": str(buyer.id),
                "developer_party_id": str(developer.id),
                # The reportable bucket, from the fixed lead-source set. Who actually
                # told us is the informant, set below, and it has its own vocabulary.
                "source": "other",
                "estimated_value": "2400000",
                "location": "Bandar Bukit Raja, Klang, Selangor",
                "notes": "Sanitaryware and shower package for towers A and B.",
            },
        )
        db.flush()
    # Assigned but not accepted, and assigned a while ago, so the wait is visible the
    # moment the queue is opened rather than reading as zero hours.
    waiting.owner_user_id = actor
    waiting.acceptance_state = "assigned"
    waiting.assigned_at = datetime.utcnow() - timedelta(hours=31)
    waiting.accepted_at = None
    waiting.informant_source = "bci"
    waiting.informant_ref = "BCI-MY-2026-08841"
    waiting.informant_contact_name = "Lim Wei Sheng"
    out["awaiting_acceptance"] = f"{waiting.lead_code} {waiting.title}"

    unassigned = (
        db.query(ProjectLead).filter(ProjectLead.title == LEAD_UNASSIGNED_TITLE).first()
    )
    if not unassigned:
        unassigned = leads_service.create_lead(
            db,
            company_id=SORENTO_COMPANY_ID,
            actor_user_id=actor,
            payload={
                "title": LEAD_UNASSIGNED_TITLE,
                # No buyer. Registered from a tip long before anyone knows who will
                # place the order, which is the case the nullable buyer exists for.
                "customer_id": None,
                "source": "architect",
                "estimated_value": "890000",
                "location": "Kepong, Kuala Lumpur",
                "notes": "Mentioned by the architect on the Ara Jaya job.",
            },
        )
        db.flush()
    unassigned.owner_user_id = None
    unassigned.acceptance_state = None
    unassigned.assigned_at = None
    unassigned.informant_source = "architect"
    unassigned.informant_contact_name = "Ar. Nurul Huda"
    out["unassigned"] = f"{unassigned.lead_code} {unassigned.title}"

    db.flush()
    return out


# --------------------------------------------------------------------------- reset


def reset(db: Session) -> dict:
    """Remove only what this script creates, found by the same natural keys."""
    removed = {}

    project = db.query(Project).filter(Project.title == PROJECT_TITLE).first()
    if project:
        quotations = (
            db.query(ProjectQuotation)
            .filter(ProjectQuotation.project_id == project.id)
            .all()
        )
        for quotation in quotations:
            versions = (
                db.query(ProjectQuotationVersion)
                .filter(ProjectQuotationVersion.quotation_id == quotation.id)
                .all()
            )
            for version in versions:
                db.query(ProjectQuotationLine).filter(
                    ProjectQuotationLine.version_id == version.id
                ).delete(synchronize_session=False)
                db.delete(version)
            db.delete(quotation)
        removed["quotations"] = len(quotations)
        db.flush()
        db.delete(project)
        removed["project"] = PROJECT_TITLE

    for title in (LEAD_WAITING_TITLE, LEAD_UNASSIGNED_TITLE):
        lead = db.query(ProjectLead).filter(ProjectLead.title == title).first()
        if lead:
            db.delete(lead)
            removed.setdefault("leads", []).append(title)

    db.commit()
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="delete the seeded rows first")
    args = parser.parse_args()

    db = SessionLocal()
    set_company_scope(db, {SORENTO_COMPANY_ID})
    try:
        if args.reset:
            print("removed:", json.dumps(reset(db), indent=2, default=str))
        print("seeded:", json.dumps(seed(db), indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
