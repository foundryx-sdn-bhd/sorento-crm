"""The agent column: recognised, carried, stamped, and used to classify when nothing else can.

The captain's real outstanding-SO export carries 20 columns and seven of them resolved to
nothing, so every upload reported them as unrecognised - `Agent` among them. That one is not
cosmetic: SO375073 in his file states no order type and names a debtor with no market
segment, so it was reported unclassifiable while the salesperson who sold it was written in
a column the importer had thrown away.

Four promises are pinned here.

1. Every column in his file resolves (AC-1.1). The six that have nothing to write to are
   deliberately-ignored aliases, which is what makes them stop being reported.
2. The agent code reaches the write path and lands on the order (AC-6.5).
3. It is the THIRD classifier, after the header's own type and the type stated in the file
   and AHEAD of the customer's market segment (captain, 28 Aug 2026: the sales force is split
   by channel, a debtor buys through both) - and only when all four miss is the document
   reported (AC-3.1 / AC-3.2).
4. An agent nobody holds is created and REPORTED, never guessed at and never a reason to
   refuse the file (AC-6.4 / AC-3.3).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.models.access import MarketSegment
from app.models.order import Customer, SalesOrder
from app.models.sales_agent import SalesAgent
from app.services.import_alias_service import AliasResolver
from app.services.scm import outstanding_import_service as svc
from app.services.scm import sales_agent_service as agents
from app.services.scm.demand_class import DEFAULT_DEMAND_CLASS, PROJECT
from app.services.scm.outstanding_reader import SO, read_workbook
from tests._pg_fixture import pg_session
from tests.scm._outstanding_workbooks import (
    MARKER,
    Codes,
    make_codes,
    seed_catalogue,
    workbook,
)

# The captain's real export, header for header. Nineteen come from the AutoCount sales-order
# detail listing (migration 338's docstring records the same row); `IB From POKey` is the
# twentieth, and its header cell really does read `POKey` even though the feedback note wrote
# `IB From PO`. Column ORDER is irrelevant to alias resolution and is not what this asserts -
# the point is that no header in a real file is left unrecognised.
CAPTAIN_HEADERS = (
    "Doc No", "Doc Date", "Delivery Date", "Debtor Code", "Debtor Name", "Agent",
    "Ref Doc No", "Ref", "Remark 1", "Item Code", "Item Description", "Location",
    "Qty", "Transfered Qty", "Remaining Qty", "Unit Price", "Discount", "Total (Inc)",
    "Note", "IB From POKey",
)

# The short header set the classification scenarios use: the document, who it is for, who
# sold it, and enough to place one line. Anything more only adds ways to fail for reasons
# these tests are not about.
AGENT_HEADERS = ("S/O NO", "DEBTOR CODE", "AGENT", "ITEM CODE", "QTY", "DELIVERY DATE",
                 "STOCK LOCATION")


@pytest.fixture()
def db():
    with pg_session() as s:
        yield s


@pytest.fixture()
def codes() -> Codes:
    return make_codes()


@pytest.fixture()
def seeded(db, codes):
    seed_catalogue(db, codes)
    return codes


def _u() -> str:
    return str(uuid.uuid4())


def _agent_code(stem: str = "SEAN") -> str:
    """An agent code shaped like the captain's (`SEAN III`), owned by this test."""
    return f"{MARKER}-{stem}-{uuid.uuid4().hex[:6]} III".upper()


def _upload(codes: Codes, doc: str, debtor: str, agent: str, *, qty: float = 40,
            order_type=None) -> bytes:
    """A one-line extract naming an agent, and stating an order type only when the case
    calls for one - this file is about what the AGENT can answer, so the header stays
    silent by default."""
    return workbook(
        [(doc, debtor, agent, codes.item_rl, qty, date(2026, 7, 1), codes.loc_project,
          order_type)],
        headers=AGENT_HEADERS + ("ORDER TYPE",),
    )


def _order(db, so_number: str, **kwargs) -> str:
    oid = _u()
    db.add(SalesOrder(id=oid, so_number=so_number, status="open", **kwargs))
    db.flush()
    return oid


def _row(db, so_number: str):
    return db.execute(
        text("SELECT demand_class, sales_agent_id FROM sales_orders WHERE so_number = :n"),
        {"n": so_number},
    ).first()


def _reported(payload: dict) -> list[str]:
    """Everything this upload told a human about, flattened to searchable text.

    Three lists upstream for good reasons; a test about "was anyone told" must not pin which
    one carried the message.
    """
    out: list[str] = []
    for item in (list(payload.get("resolution_issues") or [])
                 + list(payload.get("row_problems") or [])
                 + list(payload.get("unmapped_agents") or [])):
        out.append(" ".join(str(v) for v in item.values()))
    return out


def _classified_agent(db, code: str, demand_class: str) -> str:
    agent = agents.resolve_or_create(db, code, source=agents.MANUAL_SOURCE)
    agents.set_demand_class(db, code, demand_class)
    return agent.sales_agent


#: The last code `_unknown_debtor` handed out, so a test can assert the refusal names it
#: without threading the value through three call sites.
_last_debtor = [""]


def _unknown_debtor() -> str:
    """A debtor code no customer holds, so the market-segment fallback cannot answer."""
    _last_debtor[0] = f"{MARKER}-NOCUST-{uuid.uuid4().hex[:8]}".upper()
    return _last_debtor[0]


# --------------------------------------------------------------------------- #
# 1. every column in the real file is recognised (AC-1.1)
# --------------------------------------------------------------------------- #

def test_the_captains_twenty_column_header_leaves_nothing_unrecognised(db, seeded):
    """An unrecognised column is the first sign an export changed, so the list has to be
    empty on a file we HAVE seen - otherwise it cries wolf every week and stops being read.

    Six of the seven that used to appear here have nothing in this schema to write to; they
    resolve to fields the outstanding reader does not consume, which is the same mechanism
    `Unit Price` and `Note` already use. `Agent` is the exception: it resolves to a field the
    reader carries through, because AC-3 spends it.
    """
    file = workbook(
        [("SO-X", date(2026, 5, 4), date(2026, 7, 1), "300-T012", "TUJU RESIDENCE",
          "SEAN III", "REF-1", "REF", "remark", seeded.item_rl, "Wall tile 600x600",
          seeded.loc_project, 135, 0, 135, 12.5, 0, 1687.5, "note", "PO-KEY-1")],
        headers=CAPTAIN_HEADERS,
    )

    res = read_workbook(file, SO, AliasResolver.for_doc_type(db, SO))

    assert res.unmapped_headers == [], (
        f"the real export still reports unrecognised columns: {res.unmapped_headers}")
    assert res.ok, res.missing_columns
    assert len(res.lines) == 1


def test_the_agent_column_reaches_the_write_path(db, seeded):
    """Resolving a header is not the same as carrying its value. `Ref` resolves and is
    dropped on purpose; `Agent` has to survive to the row the importer writes."""
    file = _upload(seeded, seeded.project_so, "300-T012", "SEAN III")

    res = read_workbook(file, SO, AliasResolver.for_doc_type(db, SO))

    assert [e["agent"] for e in res.extras.values()] == ["SEAN III"]


# --------------------------------------------------------------------------- #
# 2. the order records who sold it (AC-6.5)
# --------------------------------------------------------------------------- #

def test_the_resolved_agent_is_stamped_on_the_sales_order(db, seeded):
    """Without the link the agent is a string in a spreadsheet: nothing can group demand by
    salesperson, and the classification in AC-3 has no row to read a class off.

    The agent is CLASSIFIED here so the upload has an answer at all: since QP1 a file whose
    order nothing can classify is refused, and this debtor carries no market segment. What
    is under test is the link, not the classification."""
    code = _classified_agent(db, _agent_code(), DEFAULT_DEMAND_CLASS)

    out = svc.apply(db, _upload(seeded, seeded.project_so, "300-T012", code), SO)

    assert out["ok"]
    agent = agents.resolve(db, code)
    assert agent is not None
    # str() on both sides: the raw read returns a uuid object, the ORM a string.
    assert str(_row(db, seeded.project_so).sales_agent_id) == str(agent.id)


def test_an_agent_code_nobody_holds_is_created_and_reported_never_blocked(db, seeded):
    """AC-6.4. The captain has 38 codes and no way to enter them before the first upload.

    An UNKNOWN agent code never blocks anything: it is created and reported. What blocks a
    file is an order nothing can classify (QP1), which is a different fact and is why the
    order here states its own type - a brand-new agent beside a classifiable order must
    still import.
    """
    code = _agent_code("BRANDNEW")

    preview = svc.preview(
        db, _upload(seeded, seeded.project_so, "300-T012", code, order_type="DEALER"), SO,
    ).to_dict()
    applied = svc.apply(
        db, _upload(seeded, seeded.project_so, "300-T012", code, order_type="DEALER"), SO,
    )

    assert applied["ok"], "an unknown agent code refused the whole file"
    created = agents.resolve(db, code)
    assert created is not None and created.source == agents.IMPORT_SOURCE
    assert created.demand_class is None
    assert any(code in line and "new agent" in line for line in _reported(preview)), (
        f"the preview never mentioned the new agent: {_reported(preview)}")
    assert any(code in line for line in _reported(applied)), (
        "the commit created an agent without naming it")


def test_previewing_a_file_never_creates_an_agent(db, seeded):
    """Test writes nothing. A preview that quietly seeded the master would make "what would
    change" a lie, and re-previewing a wrong file would leave the rows behind."""
    code = _agent_code("PREVIEWONLY")

    svc.preview(db, _upload(seeded, seeded.project_so, "300-T012", code), SO)

    assert agents.resolve(db, code) is None


# --------------------------------------------------------------------------- #
# 3. the agent classifies when nothing else does (AC-3.1 / AC-3.2)
# --------------------------------------------------------------------------- #

def test_an_order_with_no_type_and_no_segment_is_classified_from_its_agent(db, seeded):
    """This is SO375073 in the captain's file, and the whole reason the column was wanted.

    Nothing on the document says what the demand is for, and the debtor has no segment - but
    the salesperson only sells one kind of work, and the captain knows which. Once he says so
    on the master, the order classifies instead of being reported for ever.
    """
    code = _classified_agent(db, _agent_code("PROJECTSELLER"), PROJECT)

    out = svc.apply(db, _upload(seeded, seeded.project_so, _unknown_debtor(), code), SO)

    assert out["ok"]
    assert _row(db, seeded.project_so).demand_class == PROJECT


def test_the_agents_class_is_the_last_word_not_the_first(db, seeded):
    """Precedence, in the direction that matters: the document beats the salesperson.

    An agent who mostly sells projects will still sell the occasional trade order, and the
    header's own `order_type` is the statement about THIS order. Reading the agent first
    would overwrite a fact with a tendency.
    """
    code = _classified_agent(db, _agent_code("PROJECTSELLER"), PROJECT)
    _order(db, seeded.dealer_so, order_type="dealer")

    svc.apply(db, _upload(seeded, seeded.dealer_so, _unknown_debtor(), code), SO)

    assert _row(db, seeded.dealer_so).demand_class == DEFAULT_DEMAND_CLASS


def test_an_agent_with_no_class_lands_unclassified_and_names_the_document(db, seeded):
    """AC-3.2. All 38 codes ship with `demand_class` NULL, so this is the state on day one.

    An agent nobody has classified contributes nothing, and with the header, the file and
    the debtor silent too there is no answer left. Defaulting it to retail would
    under-prioritise a project order invisibly, and the wrong answer would be stable, so no
    later upload would surface it either - the document lands with `demand_class` NULL
    instead.

    Superseded 2026-09-06 (D23, captain ruling, AC-P2-8): QP1 made this REFUSE the whole
    file; D23 reverses that - the document lands (this test's original docstring called that
    outcome "a column the captain has struck out", which the captain's own 2026-09-06 ruling
    un-strikes) and is reported by name so "give this customer a market segment, or give the
    agent a class" is still the fix, just no longer a precondition for the rest of the book.
    """
    code = _agent_code("UNCLASSIFIED")
    agents.resolve_or_create(db, code, source=agents.MANUAL_SOURCE)

    preview = svc.preview(db, _upload(seeded, seeded.project_so, _unknown_debtor(), code),
                          SO).to_dict()
    applied = svc.apply(db, _upload(seeded, seeded.project_so, _unknown_debtor(), code), SO)

    assert preview["ok"], "an unclassifiable document must not block the confirm screen"
    # S1 (review re-check, 2026-09-06): preview now reports the same shape apply
    # does (count + capped numbers), not the raw list - was a silent preview vs
    # apply contract mismatch the FE had to special-case.
    assert preview["unclassified_documents"] == 1, preview
    assert preview["unclassified_documents_numbers"] == [seeded.project_so], preview
    assert applied["ok"], applied
    assert applied["unclassified_documents"] == 1, applied
    assert applied["unclassified_documents_numbers"] == [seeded.project_so], applied
    row = _row(db, seeded.project_so)
    assert row is not None, "D23: the order must land, not be refused"
    assert row.demand_class is None, "nothing must guess a class for it"
    # BOTH sources named, because either one is a fix the operator can make: give the
    # customer a market segment, or give the agent a class. A message naming only one
    # sends them to whichever desk happens to be mentioned.
    assert any(code in line and _last_debtor[0] in line for line in _reported(applied)), (
        f"the report named neither the debtor nor the agent: {_reported(applied)}")
    assert any(seeded.project_so in line for line in _reported(preview)), (
        f"the preview showed nothing about an order it cannot classify: {_reported(preview)}")
    assert any(code in line for line in _reported(applied)), (
        "the report never named the agent that could have answered")


def test_an_agent_who_sells_retail_classifies_retail(db, seeded):
    """The other half of the vocabulary. A guard against "any agent means project", which
    would pass the test above while being wrong for half the master."""
    code = _classified_agent(db, _agent_code("TRADESELLER"), DEFAULT_DEMAND_CLASS)

    svc.apply(db, _upload(seeded, seeded.dealer_so, _unknown_debtor(), code), SO)

    assert _row(db, seeded.dealer_so).demand_class == DEFAULT_DEMAND_CLASS


def test_the_agents_class_beats_the_customers_market_segment(db, seeded):
    """Captain, 28 Aug 2026: the seller decides before the buyer. The sales force is split
    by channel, so a project agent's order IS project work - while one debtor buys through
    both channels, so its segment is only a default about the account. This is SO381895:
    customer master said retail, agent CONTACT N sells project, and the fulfilment board could
    not see the order."""
    seg = f"{MARKER}-RETAIL-{uuid.uuid4().hex[:6]}".lower()
    db.add(MarketSegment(id=_u(), code=seg, name=seg, is_active=True))
    db.flush()
    debtor = f"{MARKER}-C-{uuid.uuid4().hex[:8]}".upper()
    db.add(Customer(id=_u(), customer_code=debtor, customer_name=debtor,
                    market_segment_code=seg, is_active=True))
    db.flush()
    code = _classified_agent(db, _agent_code("PROJECTSELLER"), PROJECT)

    svc.apply(db, _upload(seeded, seeded.project_so, debtor, code), SO)

    assert _row(db, seeded.project_so).demand_class == PROJECT


def test_the_customers_market_segment_answers_when_the_agent_carries_no_class(db, seeded):
    """The segment keeps its place as the LAST word, not no word: an agent nobody has
    classified contributes nothing, and the buyer's segment then classifies the order
    instead of the file being refused."""
    seg = f"{MARKER}-PROJECT-{uuid.uuid4().hex[:6]}".lower()
    db.add(MarketSegment(id=_u(), code=seg, name=seg, is_active=True))
    db.flush()
    debtor = f"{MARKER}-C-{uuid.uuid4().hex[:8]}".upper()
    db.add(Customer(id=_u(), customer_code=debtor, customer_name=debtor,
                    market_segment_code=seg, is_active=True))
    db.flush()
    code = _agent_code("UNCLASSIFIED")
    agents.resolve_or_create(db, code, source=agents.MANUAL_SOURCE)

    out = svc.apply(db, _upload(seeded, seeded.project_so, debtor, code), SO)

    assert out["ok"]
    assert _row(db, seeded.project_so).demand_class == PROJECT


# --------------------------------------------------------------------------- #
# 4. the unmapped-agent report (AC-3.3)
# --------------------------------------------------------------------------- #

def test_the_preview_lists_the_agents_that_cannot_classify_anything(db, seeded):
    """The captain fills the map in; the Test result is where he learns which rows need him.

    A classified agent must NOT appear: a list that names all 38 every week is a list nobody
    reads, and then the four that actually need attention are invisible.
    """
    unclassified = _agent_code("UNCLASSIFIED")
    agents.resolve_or_create(db, unclassified, source=agents.MANUAL_SOURCE)
    classified = _classified_agent(db, _agent_code("KNOWN"), PROJECT)

    file = workbook(
        [(seeded.project_so, "300-T012", unclassified, seeded.item_rl, 40,
          date(2026, 7, 1), seeded.loc_project),
         (seeded.dealer_so, "300-A031", classified, seeded.item_wt, 7,
          date(2026, 10, 30), seeded.loc_dealer)],
        headers=AGENT_HEADERS,
    )

    listed = svc.preview(db, file, SO).to_dict()["unmapped_agents"]

    assert [a["code"] for a in listed] == [unclassified]
    assert listed[0]["is_new"] is False


# --------------------------------------------------------------------------- #
# 5. one document, two agents: first wins, and it is SAID
# --------------------------------------------------------------------------- #

def _two_agent_rows(codes: Codes, doc: str, first: str, second: str) -> bytes:
    """One document, two lines, an agent code on each."""
    return workbook(
        [(doc, "300-T012", first, codes.item_rl, 40, date(2026, 7, 1), codes.loc_project),
         (doc, "300-T012", second, codes.item_wt, 7, date(2026, 10, 30), codes.loc_project)],
        headers=AGENT_HEADERS,
    )


def test_a_document_naming_two_agents_uses_the_first_and_reports_the_conflict(db, seeded):
    """The counterparty rule, applied to the column that decides fulfilment priority.

    An order has one salesperson, so a file stating two is a file to fix. First-wins is the
    right write - half a document attributed to whoever the export happened to list second
    would be worse - but SILENT first-wins is the failure: the two agents can carry different
    demand classes, so the priority of the order would be decided by row order, invisibly and
    stably, and nothing would ever surface it.
    """
    project_seller = _classified_agent(db, _agent_code("SEANONE"), PROJECT)
    trade_seller = _classified_agent(db, _agent_code("SEANTHREE"), DEFAULT_DEMAND_CLASS)
    file = _two_agent_rows(seeded, seeded.project_so, project_seller, trade_seller)

    preview = svc.preview(db, file, SO).to_dict()
    applied = svc.apply(db, file, SO)

    row = _row(db, seeded.project_so)
    assert str(row.sales_agent_id) == str(agents.resolve(db, project_seller).id), (
        "the second agent named on the document won the write")
    assert row.demand_class == PROJECT, "the class came from the second agent, not the first"
    for payload, where in ((preview, "preview"), (applied, "commit")):
        assert any(project_seller in line and trade_seller in line
                   and "the first is being used" in line
                   for line in _reported(payload)), (
            f"the {where} never named both agents on a document that states two: "
            f"{_reported(payload)}")


def test_a_document_restating_one_agent_on_every_row_reports_nothing(db, seeded):
    """The shape of EVERY real file: the export repeats the agent on all of a document's
    lines. A conflict report that fired on that would name all 4,349 rows and be worth
    nothing."""
    code = _classified_agent(db, _agent_code("SEANONE"), PROJECT)
    file = _two_agent_rows(seeded, seeded.project_so, code, f"  {code.lower()} ")

    preview = svc.preview(db, file, SO).to_dict()

    assert not any("the first is being used" in line for line in _reported(preview)), (
        f"one agent, spelled two ways, was reported as two agents: {_reported(preview)}")
    assert preview["unmapped_agents"] == []


# --------------------------------------------------------------------------- #
# 6. the importer keys agents by the master's own normaliser
# --------------------------------------------------------------------------- #

def test_the_import_resolves_agents_through_the_masters_normaliser_not_a_copy(
        db, seeded, monkeypatch):
    """One authority for the agent key, not two functions that happen to agree.

    The import's read site and the master's lookup were two separate `strip().upper()` lines
    in two modules. They agreed by coincidence: the day either learned to collapse inner
    whitespace - which is a perfectly reasonable thing to want, since `SEAN  III` and
    `SEAN III` are one agent to a human - the importer would key every document under a
    string the master had never heard of. Every agent would report as new, a duplicate master
    row would be created on every upload, and the client's demand classes would sit stranded
    on the rows nothing resolved to any more.

    Pinned by MOVING the authority: `normalize_code` is replaced with one that does collapse
    inner whitespace, and the file is spelled with a doubled space. If the importer still
    reaches the row the master holds, it asked the master; if it kept a private copy, the
    lookup misses and this fails.
    """
    real = agents.normalize_code
    monkeypatch.setattr(agents, "normalize_code",
                        lambda code: " ".join(real(code).split()))
    code = _classified_agent(db, _agent_code("SPACED"), PROJECT)
    spelled = f"  {code.lower().replace(' ', '   ')} "
    before = db.query(SalesAgent).count()

    out = svc.apply(db, _upload(seeded, seeded.project_so, _unknown_debtor(), spelled), SO)

    assert out["ok"]
    assert _row(db, seeded.project_so).demand_class == PROJECT, (
        "the importer keyed the agent under a spelling the master does not answer to")
    assert db.query(SalesAgent).count() == before, (
        "the same agent, spelled differently, created a second master row")


# --------------------------------------------------------------------------- #
# 4b. the Friday shape: a real customer who simply states no segment
# --------------------------------------------------------------------------- #
#
# The one migration 425 could not reach. 425 gave a segment to every customer a NULL-class
# order named; a customer whose orders were all classified some other way still states
# none, and the real export carries no order type column - so the AGENT is the only source
# left, and JACKSON I / JACKSON IV are the two who cannot answer (migration 427).


def test_a_customer_with_no_segment_is_carried_by_a_classified_agent(db, seeded):
    """Accepted, and classified from the agent. No silent default anywhere: the class is
    the agent's own stated one."""
    debtor = f"{MARKER}-BLANKSEG-{uuid.uuid4().hex[:8]}".upper()
    db.add(Customer(id=_u(), customer_code=debtor, customer_name=debtor, is_active=True))
    db.flush()
    code = _classified_agent(db, _agent_code("CARRIER"), DEFAULT_DEMAND_CLASS)

    out = svc.apply(db, _upload(seeded, seeded.project_so, debtor, code), SO)

    assert out["ok"], out.get("unclassified_documents")
    assert _row(db, seeded.project_so).demand_class == DEFAULT_DEMAND_CLASS


def test_a_customer_with_no_segment_and_a_blank_agent_lands_unclassified_and_names_both(
    db, seeded
):
    """Unclassified only when BOTH are blank, and the report has to name both - this is
    JACKSON I on the live book, and neither half is guessable.

    Superseded 2026-09-06 (D23, captain ruling, AC-P2-8): QP1 made this REFUSE the whole
    file; D23 reverses that - the document lands with `demand_class` NULL and is named on
    the success response instead.
    """
    debtor = f"{MARKER}-BLANKSEG-{uuid.uuid4().hex[:8]}".upper()
    db.add(Customer(id=_u(), customer_code=debtor, customer_name=debtor, is_active=True))
    db.flush()
    code = _agent_code("ALSOBLANK")
    agents.resolve_or_create(db, code, source=agents.MANUAL_SOURCE)

    out = svc.apply(db, _upload(seeded, seeded.project_so, debtor, code), SO)

    assert out["ok"], out
    assert out["unclassified_documents"] == 1, out
    assert out["unclassified_documents_numbers"] == [seeded.project_so], out
    row = _row(db, seeded.project_so)
    assert row is not None, "D23: the order must land, not be refused"
    assert row.demand_class is None
    reported = " ".join(str(v) for p in out["row_problems"] for v in p.values())
    assert debtor in reported, "the report never named the customer"
    assert code in reported, "the report never named the agent"
