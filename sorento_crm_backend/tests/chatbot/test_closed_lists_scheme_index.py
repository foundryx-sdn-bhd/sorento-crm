"""CHAT-CLOSED-LISTS SCHEME-INDEX (crew T2, 17 Oct; TRAIN shapes, synthetic codes).

"span certificate" read as the listed scheme SPAN alone: the leg became
`{"certificate": {"scheme": "SPAN"}}`, the product has no SPAN certificate, and nothing was
sent. A listed scheme is an index into certificates (owner c04 / c19), never a filter that
empties the answer. Data only, through the explicit link (product, attachment, certificate
revision, certificate.scheme): the asked products holding a certificate of the scheme get
those files; holding none, the ask is the certificate kind and every certificate file of
theirs is sent. A scheme ask over a held product keeps that product in the fetch.
"""
from __future__ import annotations

import json

import pytest
import uuid
from typing import Any

import app.main  # noqa: F401  isort:skip
from app.models.certificate import Certificate, CertificateRevision
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401

CODE = "ZZTSQ7615"


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT WC", company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()), product_code=CODE, product_name="ZZT one piece WC", category_id=cat.id,
        base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(product)
    cert_type = AttachmentType(
        id=str(uuid.uuid4()), type_name="ZZT Certification", description="Certification by Purchasing",
        allowed_extensions="pdf", is_certificate=True,
    )
    db.add(cert_type)
    db.flush()
    att = Attachment(
        id=str(uuid.uuid4()), original_filename=f"{CODE}_PPS.pdf", stored_filename=uuid.uuid4().hex,
        file_path="https://cdn.test/pps.pdf", attachment_type_id=cert_type.id, is_deleted=False, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(att)
    db.flush()
    db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=att.id, company_id=DEFAULT_COMPANY_ID))
    certs = {}
    for scheme in ("ZZPPS", "ZZSPAN"):
        cert = Certificate(
            id=str(uuid.uuid4()), attachment_type_id=cert_type.id, scheme=scheme,
            certificate_number=uuid.uuid4().hex[:10], status="active", company_id=None,
        )
        db.add(cert)
        db.flush()
        certs[scheme] = cert.id
    # Only the ZZPPS certificate is filed against the product's file; ZZSPAN is on file
    # for some other product's document (here: none), so it is listed but not this product's.
    db.add(CertificateRevision(id=str(uuid.uuid4()), certificate_id=certs["ZZPPS"], revision_no=1, attachment_id=att.id, is_current=True))
    db.commit()
    return {"product": product.id, **certs}


def _message(text: str, n: int) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-sch-{n}-{uuid.uuid4().hex[:6]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
            "traffic": "incoming", "message": {"type": "text", "text": text},
        },
    }


def _turns(session_factory, stub_access, monkeypatch, turns):
    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True,
        )

    queue = [verdict for _text, verdict in turns]
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: queue.pop(0))
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        MCPRuntimeClient, "call_tool", lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []})
    )
    stub_access()
    out = []
    for n, (text, _verdict) in enumerate(turns):
        calls.clear()
        out.append((engine_mod.run_turn(_envelope(message=_message(text, n)), session_factory=session_factory), list(calls)))
    return out


PRODUCT = {"raw": CODE, "hint": "product", "canonical_code": CODE, "current_message": True, "confident": True}


def _scheme(word: str, item: str) -> dict:
    return {"raw": word, "hint": "attachment_type", "canonical_code": item, "listed_item": item,
            "current_message": True, "confident": True}


def _file_ask(*entities: dict) -> dict:
    return _parser_output(
        intent_hint="check_product_attachment", domain_hint="product_attachment", user_goal="file", entities=list(entities)
    )


def _file_fetches(calls) -> list[dict]:
    return [args for name, args in calls if name == "crm_master_product_attachments_list"]


def test_a_scheme_the_product_does_not_hold_asks_for_its_certificates(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)

    [(_r, calls)] = _turns(
        session_factory, stub_access, monkeypatch, [(f"{CODE} zzspan certificate", _file_ask(PRODUCT, _scheme("zzspan", "ZZSPAN")))]
    )

    fetches = _file_fetches(calls)
    assert fetches, calls
    assert fetches[0].get("product_ids") == [ids["product"]], fetches
    assert not fetches[0].get("certificate_ids"), fetches


def test_a_scheme_the_product_holds_narrows_to_that_scheme(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)

    [(_r, calls)] = _turns(
        session_factory, stub_access, monkeypatch, [(f"{CODE} zzpps certificate", _file_ask(PRODUCT, _scheme("zzpps", "ZZPPS")))]
    )

    fetches = _file_fetches(calls)
    assert fetches and fetches[0].get("product_ids") == [ids["product"]], calls
    assert fetches[0].get("certificate_ids") == [ids["ZZPPS"]], fetches


def test_a_scheme_ask_over_a_held_product_keeps_the_product(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)
    first = _parser_output(intent_hint="check_stock", domain_hint="inventory", user_goal="stock", entities=[PRODUCT])

    (_r1, _c1), (_r2, calls) = _turns(
        session_factory, stub_access, monkeypatch,
        [(CODE, first), ("zzpps certificate", _file_ask(_scheme("zzpps", "ZZPPS")))],
    )

    fetches = _file_fetches(calls)
    assert fetches and fetches[0].get("product_ids") == [ids["product"]], calls
    assert fetches[0].get("certificate_ids") == [ids["ZZPPS"]], fetches


def test_a_scheme_set_ask_with_no_product_is_left_alone(session_factory):
    """"which item has ZZPPS cert": no product asked or held, the scheme stays the filter."""
    from app.models.base import set_company_scope
    from app.services.chatbot.head.scheme_index import index_scheme

    _seed(session_factory)
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    verdict = {"entities": [{**_scheme("zzpps", "ZZPPS"), "listed": "cert_scheme"}]}

    assert index_scheme(db, verdict, []) == (verdict, None)



# --- a scheme word never enters the code / did-you-mean path (crew, held-out class, 17 Oct) -------


def _numbered_certificates(session_factory, scheme: str, n: int) -> None:
    """Certificates whose NUMBERS carry the scheme word, as real registers do ("SPAN/...")."""
    db = session_factory()
    for i in range(n):
        db.add(Certificate(
            id=str(uuid.uuid4()), scheme=scheme, certificate_number=f"{scheme}/2025/{i:04d}", status="active", company_id=None,
        ))
    db.commit()


@pytest.mark.parametrize("on_file", [1, 3])
def test_a_listed_scheme_is_never_looked_up_as_a_certificate_record(on_file, session_factory, seeded, stub_access, monkeypatch):
    """The live parse of "SPAN certificate" with no product: the scheme pick (canonical_code
    null, listed_item the scheme) plus the certificate kind. The scheme word matched the
    certificate NUMBERS and the reply said 'Couldn't find "SPAN" (certificate). Did you
    mean: <numbers>', or printed one number as the subject."""
    _seed(session_factory)
    _numbered_certificates(session_factory, "ZZSPAN", on_file)
    span = {**_scheme("ZZSPAN", "ZZSPAN"), "canonical_code": None}
    kind = {**_scheme("certificate", "ZZT Certification"), "canonical_code": "certificate"}

    [(r, calls)] = _turns(session_factory, stub_access, monkeypatch, [("ZZSPAN certificate", _file_ask(span, kind))])

    text = (r.reply or {}).get("text") or ""
    assert "Did you mean" not in text, text
    assert "/2025/" not in text, text
