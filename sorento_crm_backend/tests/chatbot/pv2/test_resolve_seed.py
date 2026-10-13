"""The seed helpers themselves: proves a red run elsewhere is not a fixture bug."""
from sqlalchemy import text

from app.models.product import Product, chat_searchable_products

from tests.chatbot.pv2 import _resolve_seed as S


def test_seed_chain_builds_and_is_chat_searchable():
    with S.resolve_session() as db:
        cat = S.category(db, class_label="Water Closet", synonyms=["wc"])
        br = S.brand(db, "SORENTO")
        p1 = S.product(db, "SRTWC286-A", cat=cat, brand_row=br)
        S.product(db, "SRTWC286-B", cat=cat)
        atype = S.attachment_type(db, "technical_specifications", "Technical Specifications", synonyms=["td"])
        S.attach(db, p1, atype)
        S.customer(db, "C-1", "ACME TRADING")
        S.registry(db, "dim_length", "Length", "numeric", unit="mm")
        S.specs(db, p1, klass="Water Closet", dim_length=600)

        assert p1.family_code == "SRTWC286"
        assert db.query(Product).filter(chat_searchable_products()).count() == 2
        assert db.execute(text("SELECT search_synonyms FROM attachment_types")).scalar() == ["td"]
        S.create_pv2_tables(db)  # idempotent
