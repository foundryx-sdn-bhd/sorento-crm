"""Phase 2 RED tests - the ACCOUNT-LEDGER migrations (AC-3).

* `acct_ledger_0001` adds `customers.account_level` (additive, CHECK >= 1) and exposes
  `seed(session_or_bind)`: sets `account_level` from the name's `A/C <n>` marker ONLY where it is
  still NULL and the marker reads. An office-edited level is never overwritten; a second run is a
  no-op. The file is imported by path (the `353_customer_import_aliases` idiom) on a blank
  Postgres schema. `blank_session` builds the schema from the models, so the column exists there
  once `Customer.account_level` does.
* `acct_ledger_0002_vocab` publishes `chatbot_semantic_parser` as a NEW unlabelled version
  (the `chatbot_self_reference_vocab` pattern).
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session, unique_code

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
PROMPT_NAME = "chatbot_semantic_parser"


def _load(filename: str, alias: str):
    path = VERSIONS / filename
    assert path.exists(), f"{filename} does not exist"
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _customer(session, name: str) -> str:
    row = Customer(
        id=str(uuid.uuid4()),
        customer_code=unique_code("C")[:50],
        customer_name=name,
        company_id=DEFAULT_COMPANY_ID,
    )
    session.add(row)
    session.flush()
    return str(row.id)


def _level(session, customer_id: str):
    return session.execute(
        text("SELECT account_level FROM customers WHERE id = :i"), {"i": customer_id}
    ).scalar_one()


def _set_level(session, customer_id: str, level: int) -> None:
    session.execute(
        text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": customer_id}
    )


def test_0001_revision_ids_and_chain() -> None:
    module = _load("acct_ledger_0001.py", "mig_acct_ledger_0001")
    assert module.revision == "acct_ledger_0001"
    assert module.down_revision == "selfref_0001_n8n_sales_view"
    assert len(module.revision) <= 32
    parent = _load("selfref_0001_n8n_sales_view.py", "mig_selfref_parent")
    assert parent.revision == "selfref_0001_n8n_sales_view"


def test_0001_is_additive_with_a_check_constraint() -> None:
    source = (VERSIONS / "acct_ledger_0001.py").read_text(encoding="utf-8")
    assert "IF NOT EXISTS" in source
    assert "account_level >= 1" in source
    assert "SMALLINT" in source.upper()


def test_seed_sets_levels_from_the_marker_only_where_null() -> None:
    module = _load("acct_ledger_0001.py", "mig_acct_ledger_0001_seed")
    with blank_session() as session:
        one = _customer(session, "ZZT SOON HENG HARDWARE CO.SDN.BHD. [A/C I]")
        two = _customer(session, "ZZT SOON HENG HARDWARE CO.SDN.BHD. [A/C II]")
        bare = _customer(session, "ZZT SOON HENG HARDWARE CO.SDN.BHD.")
        project = _customer(session, "ZZT ACME TILES (PROJECT)")
        edited = _customer(session, "ZZT SOON HENG HARDWARE CO.SDN.BHD. [A/C III]")
        _set_level(session, edited, 7)

        module.seed(session.connection())

        assert _level(session, one) == 1
        assert _level(session, two) == 2
        assert _level(session, bare) is None
        assert _level(session, project) is None
        assert _level(session, edited) == 7, "an office-edited level is never overwritten"


def test_seed_twice_is_idempotent() -> None:
    module = _load("acct_ledger_0001.py", "mig_acct_ledger_0001_seed_twice")
    with blank_session() as session:
        a = _customer(session, "ZZT ROMAN EMPIRE HOME SDN BHD [A/C II]-( KL OUTLET)")
        b = _customer(session, "ZZT ESAGRAND MARKETING SDN BHD (A/C 2)")
        c = _customer(session, "ZZT NOTHING HERE SDN BHD")
        module.seed(session.connection())
        module.seed(session.connection())
        assert (_level(session, a), _level(session, b), _level(session, c)) == (2, 2, None)


def test_seed_leaves_a_level_the_office_changed_between_runs() -> None:
    """A level the office changed between two runs is left alone by the second run."""
    module = _load("acct_ledger_0001.py", "mig_acct_ledger_0001_seed_edit")
    with blank_session() as session:
        a = _customer(session, "ZZT HOME TILES PLT (A/C I)")
        module.seed(session.connection())
        assert _level(session, a) == 1
        _set_level(session, a, 4)
        module.seed(session.connection())
        assert _level(session, a) == 4


# ------------------------------------------------------------------ 0002 vocab publish


def test_0002_revision_ids_and_chain() -> None:
    module = _load("acct_ledger_0002_vocab.py", "mig_acct_ledger_0002")
    assert module.revision == "acct_ledger_0002_vocab"
    assert module.down_revision == "acct_ledger_0001"
    assert len(module.revision) <= 32
    assert callable(module.publish)


def _production_label(session):
    return (
        session.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def test_0002_publishes_a_new_version_label_unmoved_and_idempotent() -> None:
    module = _load("acct_ledger_0002_vocab.py", "mig_acct_ledger_0002_publish")
    with blank_session() as session:
        from app.services.ai_prompt_registry import PROMPT_KEYS
        from app.services.ai_prompt_seed import seed_prompt_registry

        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=1,
                type="text",
                template="STALE PROMPT TEXT (before account)",
                variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
            )
        )
        session.commit()
        seed_prompt_registry(session.get_bind())
        before = _production_label(session)
        assert before is not None
        version_before = before.version_id

        first = module.publish(session)
        assert isinstance(first, int) and first >= 2, first
        assert module.publish(session) is None

        published = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == first)
            .one()
        )
        assert "CUSTOMER ACCOUNT NUMBER" in published.template
        session.expire_all()
        after = _production_label(session)
        assert after is not None and after.version_id == version_before
