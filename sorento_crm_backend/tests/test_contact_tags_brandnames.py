"""CONTACT-TAGS final re-check: another company's brand NAME never reaches a company-scoped viewer
through apply preview rows (changes[].removed / added / before / after)."""
from __future__ import annotations

from sqlalchemy import text

from tests.test_contact_tags_s1 import CONTACTS, EDIT, VIEW, client, db, perms  # noqa: F401
from tests.test_contact_tags_s2 import _brand, _holder, _stag, all_perms  # noqa: F401
from tests.test_contact_tags_review import _other_company  # noqa: F401


def test_apply_changes_never_name_another_companys_brand(client, db, perms):
    other = _other_company(db)
    foreign, own = _brand(db), _brand(db)
    db.execute(text("UPDATE brands SET company_id = :c WHERE id = :b"), {"c": other.id, "b": foreign.id})
    tag = _stag(client, db, {"brands": [own.id]})
    contact = _holder(client, db, [tag])
    contact.brand_ids = [own.id, foreign.id]
    db.flush()
    perms["granted"] = {VIEW, EDIT}
    for dry in (True, False):
        resp = client.post(f"{CONTACTS}/bulk-apply-tag-settings", json={"contact_ids": [contact.id], "dry_run": dry})
        assert resp.status_code == 200, resp.text
        assert foreign.brand_name not in resp.text, dry
    read = client.get(f"{CONTACTS}/{contact.id}/tag-settings")
    assert foreign.brand_name not in read.text
