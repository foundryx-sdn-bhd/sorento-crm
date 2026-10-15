"""SCOPE-FRAMES: three embedding rows that a company-scoped search could read across companies.

Follow-up to the #1607 security review (plan:
documentation/plans/security/PLAN-embedding-scope-frames-12oct.md). Two companies, A and B,
in every test; the caller is scoped to A and must never read B's data.

1. A `conversation_frame` embedding was always written company-less, so
   `/external/rag/search` under a contact's company scope returned EVERY company's contact
   conversation summaries. A contact-scoped search now returns no frame at all (crew
   ruling (a): a frame is one contact's private conversation), and the worker stamps the
   frame contact's company as defence in depth.
2. A hard-deleted source's embedding was never removed (the `.deleted` event dead-lettered
   on "not found"), so its row stayed retrievable. The worker now drops it, and
   `purge_orphan_embeddings` sweeps the ones already left behind.
3. A certificate embedded the codes of every product it covers, whichever company owns
   them. It now embeds only its own company's codes (a shared certificate: company-less
   products only).
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import uuid

import pytest
from sqlalchemy import text

import app.services.embedding_worker as embedding_worker
from app.models.base import set_company_scope
from app.models.certificate import Certificate, CertificateProduct
from app.models.company import Company, RespondContactCompany
from app.models.access import RespondContact
from app.models.conversation_frame import ConversationFrame
from app.models.embeddings import EmbeddingChunk, EmbeddingDocument, EmbeddingQueue
from app.services.company_scope import register_company_scope_listeners
from app.services.embedding_service import EmbeddingReadService, debtor_seed_id

register_company_scope_listeners()

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

DIM = 1536


@pytest.fixture()
def db():
    from app.database import SessionLocal

    session = SessionLocal()
    session.begin_nested()  # SAVEPOINT rolled back at teardown
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _unit_vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _seed_chunk(db, source_type: str, source_id: str, company_id, vec) -> EmbeddingDocument:
    h = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()
    doc = EmbeddingDocument(
        source_type=source_type,
        source_id=source_id,
        source_key=source_id,
        title=source_id,
        body_text=f"summary {source_id}",
        metadata_json={},
        visibility_scope="contact",
        source_hash=h,
        company_id=company_id,
        is_active=True,
    )
    db.add(doc)
    db.flush()
    db.add(
        EmbeddingChunk(
            document_id=doc.id,
            source_type=source_type,
            source_id=source_id,
            chunk_index=0,
            chunk_text=f"summary {source_id}",
            chunk_hash=h,
            embedding=vec,
            model_name="zz-test",
            model_version="1",
            embedding_provider="zz-test",
            source_hash=h,
            metadata_json={},
            company_id=company_id,
            is_current=True,
        )
    )
    db.flush()
    return doc


def _seed_product(db, company_id, code: str) -> str:
    cat_id, uom_id, pid = (str(uuid.uuid4()) for _ in range(3))
    tag = uuid.uuid4().hex[:8]
    db.execute(
        text(
            "INSERT INTO product_categories (id, category_code, category_name, is_active, company_id, created_at) "
            "VALUES (:id, :code, :code, true, :cid, now())"
        ),
        {"id": cat_id, "code": f"ZZSF-CAT-{tag}", "cid": company_id},
    )
    db.execute(
        text(
            "INSERT INTO units_of_measure (id, uom_code, uom_name, is_active, company_id, created_at) "
            "VALUES (:id, :code, :code, true, :cid, now())"
        ),
        {"id": uom_id, "code": f"ZZSFU{tag}", "cid": company_id},
    )
    db.execute(
        text(
            "INSERT INTO products "
            "(id, product_code, product_name, category_id, base_uom_id, list_price, currency, "
            " is_active, has_serial_tracking, has_batch_tracking, variant_link_manual, is_discontinued, "
            " company_id, created_at) "
            "VALUES (:id, :code, :code, :cat, :uom, 100, 'MYR', true, false, false, false, false, :cid, now())"
        ),
        {"id": pid, "code": code, "cat": cat_id, "uom": uom_id, "cid": company_id},
    )
    return pid


def _seed_contact(db, *company_ids) -> str:
    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{random.randint(10**9, 10**10 - 1)}",
        name="ZZSF contact",
    )
    db.add(contact)
    db.flush()
    for cid in company_ids:
        db.add(RespondContactCompany(respond_contact_id=contact.id, company_id=cid))
    db.flush()
    return contact.id


def _seed_frame(db, contact_id: str, summary: str = "asked about angle valves") -> str:
    frame = ConversationFrame(
        contact_id=contact_id,
        space_id="zz-space",
        channel="whatsapp",
        status="closed",
        summary=summary,
    )
    db.add(frame)
    db.flush()
    return str(frame.id)


def _queue(db, source_type: str, source_id: str, event_type: str) -> str:
    item = EmbeddingQueue(
        source_type=source_type,
        source_id=source_id,
        event_type=event_type,
        payload={"event_type": event_type, "source_type": source_type, "source_id": source_id},
        status="pending",
    )
    db.add(item)
    db.flush()
    return str(item.id)


class _SessionProxy:
    """Hands the worker the test's session; its close() is a no-op and its commit()
    flushes only, so everything stays inside the test's savepoint."""

    def __init__(self, session):
        self._s = session
        self.commits = 0

    def __getattr__(self, name):
        return getattr(self._s, name)

    def commit(self):
        self.commits += 1
        self._s.flush()

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def world(db, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    a = Company(id=str(uuid.uuid4()), name=f"ZZSF A {suffix}", code=f"SFA{suffix}")
    b = Company(id=str(uuid.uuid4()), name=f"ZZSF B {suffix}", code=f"SFB{suffix}")
    db.add_all([a, b])
    db.flush()
    vec = _unit_vector(int(suffix, 16))
    monkeypatch.setattr(embedding_worker, "SessionLocal", lambda: _SessionProxy(db))
    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda texts, *x, **k: [vec for _ in texts])
    return {"a": a.id, "b": b.id, "suffix": suffix, "vec": vec}


def _search_ids(db, world, source_type=None) -> set[str]:
    rows = EmbeddingReadService(db).search_current(world["vec"], top_k=50, source_type=source_type)
    return {chunk.source_id for chunk, _doc, _sim in rows}


def _require_test_database(db) -> None:
    """Skip unless ``db`` is a *_ci test database. For tests that run a GLOBAL function
    (the orphan purge, the frame backfill), which touch every row in the database, not
    only the rows the test seeded."""
    name = db.execute(text("SELECT current_database()")).scalar()
    if not str(name).endswith("_ci"):
        pytest.skip(f"refused: {name} is not a *_ci test database; this test runs a global sweep")


class _NamedDb:
    """Stands in for a session on a database called ``name`` (current_database() only)."""

    def __init__(self, name: str):
        self._name = name

    def execute(self, _stmt):
        name = self._name

        class _Result:
            def scalar(self):
                return name

        return _Result()


def test_global_sweep_tests_refuse_a_non_test_database():
    """The purge and backfill tests run GLOBAL functions: the purge deletes every orphan in
    the database, the backfill stamps every company-less frame. On the shared dev database
    (a prod copy, not named *_ci) the purge test deleted about 102k real orphan documents in
    one transaction before crew stopped it. Those tests must refuse anything but a *_ci test
    database (CI: sorento_ci, sorento_changed_ci, sorento_scm_ci)."""
    for name in ("sorento", "sorento_dev", "postgres", "sorento_ci_backup"):
        with pytest.raises(pytest.skip.Exception, match="not a \\*_ci test database"):
            _require_test_database(_NamedDb(name))
    for name in ("sorento_ci", "sorento_changed_ci", "sorento_scm_ci"):
        _require_test_database(_NamedDb(name))  # no refusal


def _doc(db, source_type: str, source_id: str):
    return (
        db.query(EmbeddingDocument)
        .filter(EmbeddingDocument.source_type == source_type, EmbeddingDocument.source_id == source_id)
        .first()
    )


# ---------------------------------------------------------------- (1) conversation frames


def test_rag_search_hides_a_company_less_frame_from_a_company_scope(db, world):
    """Red before the fix: a NULL-company frame (another company's contact's summary)
    came back to a caller scoped to company A."""
    legacy = str(uuid.uuid4())
    _seed_chunk(db, "conversation_frame", legacy, None, world["vec"])
    set_company_scope(db, frozenset({world["a"]}))
    assert legacy not in _search_ids(db, world, "conversation_frame")
    assert legacy not in _search_ids(db, world)  # no source_type filter either

    # Staff / unscoped key (scope None) keep reading it.
    set_company_scope(db, None)
    assert legacy in _search_ids(db, world, "conversation_frame")


def test_rag_search_never_returns_another_contacts_frame_in_the_same_company(db, world):
    """Crew ruling (a): a frame is one contact's private conversation, so a contact-scoped
    search returns no frame at all, even one stamped with the caller's own company.
    Contacts A and B both sit in company X (world "a"); A's search must not read B's
    frame. Frames stay recallable through the contact-bound /external/memory/frames/search."""
    contact_a = _seed_contact(db, world["a"])
    contact_b = _seed_contact(db, world["a"])
    frame_b = _seed_frame(db, contact_b)
    out = embedding_worker.process_embedding_queue_item(
        _queue(db, "conversation_frame", frame_b, "frame_closed")
    )
    assert out["status"] == "completed", out
    assert _doc(db, "conversation_frame", frame_b).company_id == world["a"]

    from app.services.company_scope_resolver import resolve_contact_company_scope

    scope_a = resolve_contact_company_scope(db, contact_a, "zz-space")
    assert scope_a == frozenset({world["a"]})
    set_company_scope(db, scope_a)
    assert frame_b not in _search_ids(db, world, "conversation_frame")
    assert frame_b not in _search_ids(db, world)

    set_company_scope(db, None)  # staff / unscoped key
    assert frame_b in _search_ids(db, world, "conversation_frame")


def test_rag_search_returns_no_other_companys_frame(db, world):
    frame_b = str(uuid.uuid4())
    _seed_chunk(db, "conversation_frame", frame_b, world["b"], world["vec"])
    set_company_scope(db, frozenset({world["a"]}))
    assert frame_b not in _search_ids(db, world, "conversation_frame")


def test_worker_stamps_the_frame_contacts_company(db, world):
    """Red before the fix: the frame's document and chunks were written company-less."""
    frame_id = _seed_frame(db, _seed_contact(db, world["b"]))
    out = embedding_worker.process_embedding_queue_item(
        _queue(db, "conversation_frame", frame_id, "frame_closed")
    )
    assert out["status"] == "completed", out
    doc = _doc(db, "conversation_frame", frame_id)
    assert doc.company_id == world["b"]
    chunks = db.query(EmbeddingChunk).filter(EmbeddingChunk.source_id == frame_id).all()
    assert chunks and {c.company_id for c in chunks} == {world["b"]}

    set_company_scope(db, frozenset({world["a"]}))
    assert frame_id not in _search_ids(db, world, "conversation_frame")


def test_worker_leaves_a_multi_company_contacts_frame_company_less(db, world):
    """A contact in two companies has no one company to stamp: the frame stays NULL and
    so is hidden from every company scope (fail-closed), never stamped with a guess."""
    frame_id = _seed_frame(db, _seed_contact(db, world["a"], world["b"]))
    out = embedding_worker.process_embedding_queue_item(
        _queue(db, "conversation_frame", frame_id, "frame_closed")
    )
    assert out["status"] == "completed", out
    assert _doc(db, "conversation_frame", frame_id).company_id is None
    set_company_scope(db, frozenset({world["a"]}))
    assert frame_id not in _search_ids(db, world, "conversation_frame")


def test_backfill_stamps_existing_frame_embeddings(db, world):
    """The migration's backfill: existing NULL-company frame rows get the contact's
    company; a multi-company contact's frame and a frame with no row stay NULL."""
    _require_test_database(db)
    frame_b = _seed_frame(db, _seed_contact(db, world["b"]))
    frame_multi = _seed_frame(db, _seed_contact(db, world["a"], world["b"]))
    gone = str(uuid.uuid4())
    for fid in (frame_b, frame_multi, gone):
        _seed_chunk(db, "conversation_frame", fid, None, world["vec"])

    stamped = embedding_worker.backfill_frame_embedding_companies(db)
    assert stamped >= 1
    db.expire_all()
    assert _doc(db, "conversation_frame", frame_b).company_id == world["b"]
    assert {
        c.company_id
        for c in db.query(EmbeddingChunk).filter(EmbeddingChunk.source_id == frame_b)
    } == {world["b"]}
    assert _doc(db, "conversation_frame", frame_multi).company_id is None
    assert _doc(db, "conversation_frame", gone).company_id is None


# ---------------------------------------------------------------- (2) deleted sources


def test_delete_event_removes_the_embedding(db, world):
    """Red before the fix: `<type>.deleted` raised "not found", was retried, then
    dead-lettered, and the company-less row stayed visible to company A."""
    gone = str(uuid.uuid4())
    _seed_chunk(db, "certificate", gone, None, world["vec"])
    set_company_scope(db, frozenset({world["a"]}))
    assert gone in _search_ids(db, world, "certificate")  # precondition: the leak

    set_company_scope(db, None)
    out = embedding_worker.process_embedding_queue_item(_queue(db, "certificate", gone, "certificate.deleted"))
    assert out["status"] == "removed", out
    assert _doc(db, "certificate", gone) is None
    assert db.query(EmbeddingChunk).filter(EmbeddingChunk.source_id == gone).count() == 0


def test_delete_event_for_a_row_that_still_exists_keeps_the_embedding(db, world):
    """Guard: a stale `.deleted` event for a row that is still there removes nothing."""
    pid = _seed_product(db, world["a"], f"ZZSFP{world['suffix']}")
    _seed_chunk(db, "product", pid, world["a"], world["vec"])
    out = embedding_worker.process_embedding_queue_item(_queue(db, "product", pid, "product.deleted"))
    assert out["status"] != "removed"
    assert _doc(db, "product", pid) is not None


def test_product_delete_also_removes_its_product_spec_embedding(db, world):
    """product_spec is keyed on the product's id: it goes with the product."""
    gone = str(uuid.uuid4())
    _seed_chunk(db, "product", gone, world["a"], world["vec"])
    _seed_chunk(db, "product_spec", gone, world["a"], world["vec"])
    out = embedding_worker.process_embedding_queue_item(_queue(db, "product", gone, "product.deleted"))
    assert out["status"] == "removed", out
    assert out["documents"] == 2
    assert _doc(db, "product", gone) is None
    assert _doc(db, "product_spec", gone) is None


def test_existence_check_on_a_malformed_id_does_not_poison_the_session(db, world):
    """The existence check runs in a SAVEPOINT: an id that is not a uuid fails that one
    statement and reads as "present" (nothing removed), and the session stays usable for
    the worker's next statement instead of failing on an aborted transaction."""
    assert embedding_worker._source_row_exists(db, "product", "not-a-uuid") is True
    assert db.query(Company).filter(Company.id == world["a"]).count() == 1


def test_purge_orphans_counts_on_dry_run_and_deletes_on_apply(db, world, monkeypatch, capsys):
    _require_test_database(db)
    pid = _seed_product(db, world["a"], f"ZZSFQ{world['suffix']}")
    live_frame = _seed_frame(db, _seed_contact(db, world["a"]))
    orphan_cert, orphan_frame = str(uuid.uuid4()), str(uuid.uuid4())
    debtor = f"debtor:ZZSF{world['suffix']}"  # legacy, company-less seed
    seed = debtor_seed_id(world["a"], f"ZZSF{world['suffix']}")  # #1607 per-company seed
    _seed_chunk(db, "product", pid, world["a"], world["vec"])
    _seed_chunk(db, "conversation_frame", live_frame, world["a"], world["vec"])
    _seed_chunk(db, "certificate", orphan_cert, None, world["vec"])
    _seed_chunk(db, "conversation_frame", orphan_frame, None, world["vec"])
    _seed_chunk(db, "customer", debtor, None, world["vec"])  # synthetic seed, no row by design
    _seed_chunk(db, "customer", seed, world["a"], world["vec"])

    def mine(counts_ids):
        return counts_ids & {pid, live_frame, orphan_cert, orphan_frame, debtor, seed}

    dry = embedding_worker.purge_orphan_embeddings(db)
    assert mine(set(dry["source_ids"])) == {orphan_cert, orphan_frame}
    assert dry["counts"].get("certificate", 0) >= 1
    assert dry["counts"].get("conversation_frame", 0) >= 1
    assert _doc(db, "certificate", orphan_cert) is not None  # counting deletes nothing
    # The library only counts: no one-transaction global delete (crew incident, 13 Oct).
    with pytest.raises(TypeError):
        embedding_worker.purge_orphan_embeddings(db, apply=True)

    total = sum(dry["counts"].values())
    script = _purge_script(db, monkeypatch)
    assert script.main(["--apply", "--expect", str(total)]) == 0
    db.expire_all()
    assert _doc(db, "certificate", orphan_cert) is None
    assert _doc(db, "conversation_frame", orphan_frame) is None
    assert _doc(db, "product", pid) is not None
    assert _doc(db, "conversation_frame", live_frame) is not None
    assert _doc(db, "customer", debtor) is not None
    assert _doc(db, "customer", seed) is not None


# ---------------------------------------------------------------- (3) certificate codes


def _seed_certificate(db, company_id, codes_by_company: dict) -> tuple[str, dict]:
    cert = Certificate(
        scheme="SIRIM",
        certificate_number=f"ZZSF-{uuid.uuid4().hex[:8]}",
        company_id=company_id,
    )
    db.add(cert)
    db.flush()
    for owner, code in codes_by_company.items():
        pid = _seed_product(db, owner, code)
        db.add(CertificateProduct(certificate_id=cert.id, product_id=pid, source="manual"))
    db.flush()
    return str(cert.id), codes_by_company


def test_certificate_embeds_only_its_own_companys_codes(db, world):
    s = world["suffix"]
    cert_id, _ = _seed_certificate(
        db, world["a"], {world["a"]: f"ZZSFA{s}", world["b"]: f"ZZSFB{s}"}
    )
    set_company_scope(db, None)
    src = embedding_worker._canonical_for_source(db, "certificate", cert_id)
    assert src["metadata"]["covered_products"] == [f"ZZSFA{s}"]
    assert f"ZZSFB{s}" not in src["body_text"]


def test_shared_certificate_embeds_no_company_owned_codes(db, world):
    """Red before the fix: a shared (NULL-company) certificate's company-less embedding
    carried company A's and company B's product codes to every scope."""
    s = world["suffix"]
    cert_id, _ = _seed_certificate(
        db, None, {world["a"]: f"ZZSFA{s}", world["b"]: f"ZZSFB{s}", None: f"ZZSFN{s}"}
    )
    set_company_scope(db, None)
    src = embedding_worker._canonical_for_source(db, "certificate", cert_id)
    # A company-less product is shared like the certificate, so its code stays.
    assert src["metadata"]["covered_products"] == [f"ZZSFN{s}"]
    assert f"ZZSFA{s}" not in src["body_text"]
    assert f"ZZSFB{s}" not in src["body_text"]
    chunks = embedding_worker._chunks_for_source("certificate", src)
    assert not any(f"ZZSFB{s}" in c or f"ZZSFA{s}" in c for c in chunks)


def _purge_script(db, monkeypatch):
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "purge_orphan_embeddings.py"
    spec = importlib.util.spec_from_file_location("purge_orphan_embeddings_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proxy = _SessionProxy(db)
    monkeypatch.setattr(module, "SessionLocal", lambda: proxy)
    return module


def test_purge_script_apply_needs_the_dry_run_total(db, world, monkeypatch, capsys):
    """Prod safety: `--apply` deletes only when `--expect` equals the count a dry run just
    reported, so an operator cannot apply without having read one. A mismatch deletes
    nothing and exits 2."""
    _require_test_database(db)
    orphan, orphan_2 = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_chunk(db, "certificate", orphan, None, world["vec"])
    _seed_chunk(db, "certificate", orphan_2, None, world["vec"])  # two, so batches are visible
    script = _purge_script(db, monkeypatch)
    dbname = db.execute(text("SELECT current_database()")).scalar()

    assert script.main([]) == 0  # dry run
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0] == f"database: {dbname}"  # the operator sees which database first
    total = int(out[-1].split()[2])
    assert total >= 2

    assert script.main(["--apply"]) == 2  # no --expect
    assert script.main(["--expect", str(total)]) == 2  # --expect without --apply
    assert script.main(["--apply", "--expect", str(total + 1)]) == 2  # stale count
    refusal = capsys.readouterr()
    # The refusal must not hand out the number to copy: only a dry run shows it.
    assert "refused" in refusal.err and str(total) not in refusal.err
    assert _doc(db, "certificate", orphan) is not None

    proxy = script.SessionLocal()
    assert script.main(["--apply", "--expect", str(total), "--batch-size", "1"]) == 0
    # One commit per batch: --batch-size 1 means at least one commit per document.
    assert proxy.commits >= total
    db.expire_all()
    assert _doc(db, "certificate", orphan) is None
    assert _doc(db, "certificate", orphan_2) is None
    assert f"deleted: {total} document(s)" in capsys.readouterr().out


def test_every_global_sweep_test_calls_the_test_database_guard():
    """Meta-check (review of 7931e4dd): a test that runs a global sweep must call
    _require_test_database before it, or a forgotten guard reopens the 13 Oct incident."""
    import inspect
    import sys

    module = sys.modules[__name__]
    sweeps = ("purge_orphan_embeddings(", "backfill_frame_embedding_companies(", "_purge_script(")
    checked = 0
    for name, fn in vars(module).items():
        if not (name.startswith("test_") and callable(fn)):
            continue
        body = inspect.getsource(fn)
        first_sweep = min((body.find(s) for s in sweeps if s in body), default=-1)
        if first_sweep < 0 or name == "test_every_global_sweep_test_calls_the_test_database_guard":
            continue
        checked += 1
        guard = body.find("_require_test_database(db)")
        assert 0 <= guard < first_sweep, f"{name} runs a global sweep without the *_ci guard first"
    assert checked >= 3
