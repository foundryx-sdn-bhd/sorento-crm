"""SCRATCH, not committed: full-turn repro of file asks with the real parser (gate on / off)."""
import json, time, uuid
from pathlib import Path
import pytest
import app.main  # noqa
from app.models.certificate import Certificate
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services import chatbot_prompt_vars as pv
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.ai_prompt_registry import _substitute
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.head.closed_lists_prompt import SECTION
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.product_spec_registry import seed_spec_registry
from tests.chatbot.conftest import session_factory  # noqa
from tests.chatbot.test_engine import CONTACT_ID, _envelope, seeded, stub_access  # noqa

BASE = Path("alembic/data/chatbot_semantic_parser.refkinds-v56-20261011.txt").read_text()
import os
MSGS = os.environ.get("REPRO_MSGS", "ZZTWC7615 photo|span certificate|drawing pls|ZZTWC7615 stock|cert?").split("|")
OUT = Path("/tmp/claude-0/-home-user-sorento-crm/882937c4-de6b-5e88-a4e7-022763fe636c/scratchpad/repro.txt")


def _seed(sf):
    db = sf()
    seed_spec_registry(db)
    cat = ProductCategory(id=str(uuid.uuid4()), category_code="ZZT-WC", category_name="ZZT WC", company_id=DEFAULT_COMPANY_ID, class_label="Water Closet")
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-EA", uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom]); db.flush()
    p = Product(id=str(uuid.uuid4()), product_code="ZZTWC7615", product_name="ZZT one piece WC", category_id=cat.id, base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID)
    db.add(p); db.flush()
    for name, desc, cert in (("Product Photos", "Product Photos, Photo, Image, Pictures by Marketing", False),
                             ("Product Videos", "Product Videos by Marketing", False),
                             ("Technical Specifications", "Technical Specifications / Spec / Drawing by Marketing", False),
                             ("Installation Guide", "Installation Guide by Marketing", False),
                             ("Certification", "Certification, Cert, Certificate, Watermark Cert, WCM, PPS, Ikram by Purchasing", True)):
        t = AttachmentType(id=str(uuid.uuid4()), type_name=name, code=None, description=desc, allowed_extensions="pdf", is_certificate=cert)
        db.add(t); db.flush()
        if name == "Installation Guide":
            continue
        a = Attachment(id=str(uuid.uuid4()), original_filename=f"{name}.pdf", stored_filename=uuid.uuid4().hex, file_path="https://x/z.pdf", attachment_type_id=t.id, is_deleted=False, company_id=DEFAULT_COMPANY_ID)
        db.add(a); db.flush()
        db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=p.id, attachment_id=a.id, company_id=DEFAULT_COMPANY_ID))
        if cert:
            for s in ("PPS", "IKRAM", "WCM"):
                db.add(Certificate(id=str(uuid.uuid4()), attachment_type_id=t.id, scheme=s, certificate_number=uuid.uuid4().hex[:10], status="active", company_id=None))
    db.commit()
    return {r.id: r.type_name for r in db.query(AttachmentType).all()}


@pytest.mark.live_external
@pytest.mark.parametrize("gate", [True, False])
def test_repro(gate, session_factory, seeded, stub_access, monkeypatch):
    types = _seed(session_factory)
    db = session_factory()
    lists = pv.render_values(db, ["attachment_types", "certification_types"])
    schema = parser_mod.closed_lists_schema(pv.closed_list_enum(db))
    date = time.strftime("%A, %d %B %Y")
    prompt = _substitute(BASE + (SECTION if gate else ""), {"current_date": date, **lists})
    cfg = parser_mod.ParserConfig(system_prompt=prompt, prompt_version=1, provider="openai", model="gpt-5.4-mini", api_key="proxy",
                                  closed_lists=gate, json_schema=schema if gate else None)
    monkeypatch.setattr(parser_mod, "resolve_config", lambda db, *, current_date, override_version_id=None: cfg)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    calls = []
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, n, a: calls.append((n, a)) or json.dumps({"ok": True, "items": []}))
    stub_access()
    with OUT.open("a") as fh:
        for i, text in enumerate(MSGS):
            calls.clear()
            msg = {"event_type": "message.received", "contact": {"id": CONTACT_ID},
                   "message": {"messageId": f"ZZT-r-{gate}-{i}", "contactId": CONTACT_ID, "channelId": "whatsapp", "traffic": "incoming",
                               "message": {"type": "text", "text": text}}}
            res = engine_mod.run_turn(_envelope(message=msg), session_factory=session_factory)
            from app.models.chatbot_turn import ChatbotTurn
            turn = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == res.turn_id).one()
            apply = [r for r in turn.trace or [] if r.get("kind") == "apply"]
            ents = ((apply[-1].get("verdict") or {}).get("entities") if apply else None) or []
            grounding = [r for r in turn.trace or [] if r.get("kind") == "grounding"]
            und = [r for r in turn.trace or [] if r.get("stage") == "understood"]
            raw = ((und[-1].get("raw") or {}).get("parser_raw") or {}) if und else {}
            fh.write(f"\n== gate={gate} | {text}\n")
            fh.write("  parser: " + json.dumps([{k: e.get(k) for k in ('raw','hint','canonical_code','listed_item')} for e in raw.get('entities') or []], ensure_ascii=False) + f" intent={raw.get('intent_hint')} domain={raw.get('domain_hint')}\n")
            fh.write("  grounding: " + json.dumps([g.get("changes") for g in grounding], ensure_ascii=False)[:600] + "\n")
            fh.write("  apply: " + json.dumps([{k: e.get(k) for k in ('raw','hint','canonical_code','listed')} for e in ents], ensure_ascii=False) + "\n")
            fh.write("  tools: " + json.dumps([(n, {k: (v if k != 'attachment_type_ids' else [types.get(x, x) for x in v]) for k, v in (a or {}).items() if k in ('attachment_type_ids','product_ids','certificate_ids')}) for n, a in calls], ensure_ascii=False)[:600] + "\n")
            fh.write(f"  branch={res.branch_kind} reply: " + str((res.reply or {}).get('text'))[:300].replace("\n", " / ") + "\n")
