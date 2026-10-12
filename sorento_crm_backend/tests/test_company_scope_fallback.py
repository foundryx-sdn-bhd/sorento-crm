"""The company-scope fallback attaches only the classes a statement can touch (CHAT-SPEED).

A SELECT whose top-level entity is not company-scoped used to get a `with_loader_criteria`
for every one of the ~158 scoped classes: about 14 ms of CPU per query, about 40 queries
per chatbot turn. The narrowed fallback must emit the SAME SQL as scoping every class,
which is what the second test compares, statement by statement, on the real listener.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import event, exists, func, select
from sqlalchemy.orm import Session, aliased, joinedload
from sqlalchemy.orm.util import LoaderCriteriaOption

from app.database import SessionLocal
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.product import Brand, Product
from app.models.product_set import ProductSetMember
from app.services import company_scope as cs
from app.services.company_scope import register_company_scope_listeners

register_company_scope_listeners()

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

SCOPE = frozenset({cs.DEFAULT_COMPANY_ID})


@pytest.fixture()
def db():
    session = SessionLocal()
    session.info["company_scope"] = SCOPE
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _criteria_count(db, run) -> int:
    """How many loader criteria the listener left on the statement `run` executes."""
    seen: list[int] = []

    def after(state):
        if state.is_select and not state.is_column_load and not state.is_relationship_load:
            seen.append(
                sum(isinstance(o, LoaderCriteriaOption) for o in state.statement._with_options)
            )

    # Registered after the scope listener, so it reads the statement the listener built.
    event.listen(Session, "do_orm_execute", after)
    try:
        run(db)
    finally:
        event.remove(Session, "do_orm_execute", after)
    return seen[0]


def test_an_unscoped_query_carries_no_company_criteria(db):
    def prompt_lookup(s):
        return (
            s.query(AIPromptVersion)
            .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
            .filter(AIPromptLabel.name == "zz-none", AIPromptLabel.label == "production")
            .first()
        )

    assert _criteria_count(db, prompt_lookup) == 0


def test_a_scalar_select_carries_no_company_criteria(db):
    assert _criteria_count(db, lambda s: s.execute(select(func.now())).scalar()) == 0


def test_an_unscoped_entity_with_a_joined_scoped_relationship_still_scopes_it(db):
    # `ProductSetMember.product` is lazy="joined": the Product rows come in on the same
    # statement, so Product's criterion has to stay.
    assert _criteria_count(db, lambda s: s.query(ProductSetMember).first()) > 0


QUERIES = {
    "count over a scoped entity": lambda s: s.query(Product).filter(Product.product_code == "zz").count(),
    "count over an unscoped entity": lambda s: s.query(AIPromptVersion).count(),
    "count of a scoped column": lambda s: s.query(func.count(Product.id)).scalar(),
    "exists over a scoped entity": lambda s: s.query(exists().where(Product.product_code == "zz")).scalar(),
    "select_from a scoped entity": lambda s: s.execute(select(func.count()).select_from(Product)).scalar(),
    "unscoped joined to scoped": lambda s: s.query(AIPromptVersion)
    .join(Product, Product.product_code == AIPromptVersion.name)
    .first(),
    "unscoped with a lazy=joined scoped relationship": lambda s: s.query(ProductSetMember).first(),
    "unscoped with joinedload": lambda s: s.query(ProductSetMember)
    .options(joinedload(ProductSetMember.product))
    .first(),
    "unscoped joined through a relationship": lambda s: s.query(ProductSetMember)
    .join(ProductSetMember.product)
    .first(),
    "unscoped filtered by a scoped subquery": lambda s: s.query(AIPromptVersion)
    .filter(AIPromptVersion.name.in_(s.query(Product.product_code)))
    .first(),
    "aliased scoped count": lambda s: s.query(func.count(aliased(Product).id)).scalar(),
    "two scoped tables in a union": lambda s: s.execute(
        select(Product.product_code).union_all(select(Brand.brand_code))
    ).all(),
    "the prompt registry lookup": lambda s: s.query(AIPromptVersion)
    .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
    .filter(AIPromptLabel.name == "zz")
    .first(),
    "a scalar select": lambda s: s.execute(select(func.now())).scalar(),
}


def _emitted_sql(db, run) -> list[tuple[str, str]]:
    statements: list[tuple[str, str]] = []
    bind = db.get_bind()

    def capture(conn, cursor, statement, params, context, executemany):
        statements.append((statement, repr(params)))

    event.listen(bind, "before_cursor_execute", capture)
    try:
        run(db)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
        db.rollback()
    return statements


@pytest.mark.parametrize("name", list(QUERIES))
def test_the_narrowed_fallback_emits_the_same_sql_as_scoping_every_class(db, monkeypatch, name):
    narrowed = _emitted_sql(db, QUERIES[name])
    monkeypatch.setattr(cs, "_scoped_classes_in", lambda statement: cs._all_scoped_classes())
    everything = _emitted_sql(db, QUERIES[name])
    assert narrowed == everything
