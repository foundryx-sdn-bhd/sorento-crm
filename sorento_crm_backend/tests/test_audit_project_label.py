"""FORMS-PROJECT: the record history shows a linked project as `CODE - Title`, never its UUID
(no UUIDs in the UI). Other fields of the same change are left as they are."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import text

from app.models.base import company_scope
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session


def test_project_ids_in_the_trail_read_as_code_and_title():
    from app.api.v1.audit.audit_logs import _label_project_values, _project_labels
    from app.services.project_service import register_project

    with blank_session() as db:
        srt = str(db.execute(text("select id from companies where code = 'SRT'")).scalar())
        project_seed_service.run(db, company_id=srt)
        owner = str(uuid.uuid4())
        db.add(User(id=owner, email=f"{owner}@zzt.test", name="zzt owner"))
        db.flush()
        with company_scope(db, frozenset({srt})):
            project = register_project(
                db, company_id=srt, actor_user_id=owner, developer_party_id=None,
                title="zzt Audit Tower",
            )
            db.flush()
            item = SimpleNamespace(
                old_values={"project_id": None, "remark": "a"},
                new_values={"project_id": str(project.id), "remark": "b"},
            )
            labels = _project_labels(db, [item, SimpleNamespace(old_values={"project_id": "x"}, new_values=None)])
        payload = {"old_values": dict(item.old_values), "new_values": dict(item.new_values)}
        _label_project_values(payload, labels)
    assert payload["new_values"]["project_id"] == f"{project.project_code} - zzt Audit Tower"
    assert payload["old_values"]["project_id"] is None
    assert payload["new_values"]["remark"] == "b"
    hidden = {"old_values": None, "new_values": {"project_id": str(uuid.uuid4())}}
    _label_project_values(hidden, labels)
    assert hidden["new_values"]["project_id"] == "Linked project not visible"
