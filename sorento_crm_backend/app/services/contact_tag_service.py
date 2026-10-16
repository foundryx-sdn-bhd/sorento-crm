"""Contact tag catalog and tagging (CONTACT-TAGS S1).

Tags are labels staff put on contacts. No code path here (or in sync, the external upsert or
create_contact) assigns a tag on its own. `settings` is stored for S2 and not interpreted.
"""
from __future__ import annotations

import uuid
from typing import Iterable, Optional

from sqlalchemy import and_, delete, func, insert, select
from sqlalchemy.orm import Session

from app.models.access import ContactTag, RespondContact, respond_contact_tags
from app.services.error_handler import handle_conflict, handle_not_found, handle_unprocessable

_ORDER = (ContactTag.sort_order.asc(), ContactTag.name.asc())


def tags_by_contact(db: Session, contact_ids: Iterable[str]) -> dict[str, list[dict]]:
    """`{contact_id: [{id, name}]}` ordered by tag sort_order then name; one query for the page."""
    ids = [str(i) for i in contact_ids]
    if not ids:
        return {}
    rows = db.execute(
        select(respond_contact_tags.c.contact_id, ContactTag.id, ContactTag.name)
        .join(ContactTag, ContactTag.id == respond_contact_tags.c.tag_id)
        .where(respond_contact_tags.c.contact_id.in_(ids))
        .order_by(*_ORDER)
    ).all()
    out: dict[str, list[dict]] = {}
    for contact_id, tag_id, name in rows:
        out.setdefault(str(contact_id), []).append({"id": str(tag_id), "name": name})
    return out


class ContactTagService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- catalog
    def _counts(self, tag_ids: list[str]) -> dict[str, int]:
        if not tag_ids:
            return {}
        rows = self.db.execute(
            select(respond_contact_tags.c.tag_id, func.count())
            .where(respond_contact_tags.c.tag_id.in_(tag_ids))
            .group_by(respond_contact_tags.c.tag_id)
        ).all()
        return {str(t): int(n) for t, n in rows}

    def _with_count(self, tags: list[ContactTag]) -> list[dict]:
        from app.services import contact_tag_settings as cts

        counts = self._counts([str(t.id) for t in tags])
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "description": t.description,
                "is_active": t.is_active,
                "sort_order": t.sort_order,
                "settings": t.settings or {},
                "contact_count": counts.get(str(t.id), 0),
                "differs_count": cts.differs_count(self.db, str(t.id)) if cts.leaves_of(t.settings) else 0,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in tags
        ]

    _SORTABLE = {
        "name": ContactTag.name,
        "sort_order": ContactTag.sort_order,
        "is_active": ContactTag.is_active,
        "created_at": ContactTag.created_at,
    }

    def list_tags(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        is_active: Optional[bool],
        sort: Optional[str] = None,
        direction: Optional[str] = None,
    ):
        q = self.db.query(ContactTag)
        if search and search.strip():
            q = q.filter(ContactTag.name.ilike(f"%{search.strip()}%"))
        if is_active is not None:
            q = q.filter(ContactTag.is_active.is_(is_active))
        total = q.count()
        column = self._SORTABLE.get(sort or "")
        order = _ORDER
        if column is not None:
            order = (column.desc() if direction == "desc" else column.asc(), ContactTag.name.asc())
        rows = q.order_by(*order).offset((page - 1) * limit).limit(limit).all()
        return self._with_count(rows), total

    def get_tag(self, tag_id: str) -> ContactTag:
        row = self._find(tag_id)
        if row is None:
            raise handle_not_found("Contact tag", tag_id)
        return row

    def _find(self, tag_id: str) -> Optional[ContactTag]:
        try:
            key = str(uuid.UUID(str(tag_id)))
        except ValueError:
            return None  # a malformed id cannot be a tag
        return self.db.query(ContactTag).filter(ContactTag.id == key).first()

    def get_tag_dict(self, tag_id: str) -> dict:
        return self._with_count([self.get_tag(tag_id)])[0]

    def _assert_name_free(self, name: str, own_id: Optional[str] = None) -> None:
        clash = self.db.query(ContactTag).filter(func.lower(ContactTag.name) == name.lower()).first()
        if clash is not None and str(clash.id) != str(own_id or ""):
            raise handle_conflict(f'A tag named "{clash.name}" already exists.')

    def _check_settings(self, old: dict, new: dict, gates) -> None:
        """Validate (422) first, then gate (403) the sections that changed."""
        from app.services import contact_tag_settings as cts

        cts.validate_settings(self.db, new)
        if gates is not None:
            cts.assert_gates(old, new, *gates)

    def create_tag(self, data: dict, actor_id: Optional[str], gates=None) -> dict:
        self._assert_name_free(data["name"])
        self._check_settings({}, data.get("settings") or {}, gates)
        row = ContactTag(
            name=data["name"],
            description=data.get("description"),
            is_active=data.get("is_active", True),
            sort_order=data.get("sort_order", 0),
            settings=data.get("settings") or {},
            created_by=actor_id,
            updated_by=actor_id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return self._with_count([row])[0]

    def update_tag(self, tag_id: str, data: dict, actor_id: Optional[str], gates=None) -> dict:
        row = self.get_tag(tag_id)
        if data.get("settings") is not None:
            self._check_settings(row.settings or {}, data["settings"], gates)
        if data.get("name") is not None:
            self._assert_name_free(data["name"], own_id=str(row.id))
        for key in ("name", "description", "is_active", "sort_order", "settings"):
            if key in data and (data[key] is not None or key == "description"):
                setattr(row, key, data[key])
        row.updated_by = actor_id
        self.db.commit()
        self.db.refresh(row)
        return self._with_count([row])[0]

    def delete_tag(self, tag_id: str) -> None:
        """Remove the tag; its contact links go by cascade. No contact access value changes."""
        row = self.get_tag(tag_id)
        self.db.execute(delete(ContactTag).where(ContactTag.id == str(row.id)))
        self.db.commit()

    # ---------------------------------------------------------------- tagging
    def _require_tags(self, tag_ids: Iterable[str]) -> dict[str, ContactTag]:
        wanted = list(dict.fromkeys(str(t) for t in tag_ids))
        found: dict[str, ContactTag] = {}
        for tag_id in wanted:
            row = self._find(tag_id)
            if row is None:
                raise handle_unprocessable(f"Unknown tag: {tag_id}")
            found[tag_id] = row
        return found

    def set_contact_tags(self, contact_id: str, tag_ids: list[str], actor_id: Optional[str]) -> list[dict]:
        contact = self.db.query(RespondContact).filter(RespondContact.id == contact_id).first()
        if contact is None:
            raise handle_not_found("Contact", contact_id)
        wanted = self._require_tags(tag_ids)
        self.db.execute(delete(respond_contact_tags).where(respond_contact_tags.c.contact_id == contact_id))
        if wanted:
            self.db.execute(
                insert(respond_contact_tags),
                [{"contact_id": contact_id, "tag_id": t, "created_by": actor_id} for t in wanted],
            )
        self.db.commit()
        return tags_by_contact(self.db, [contact_id]).get(contact_id, [])

    def bulk_tag(
        self,
        contact_ids: list[str],
        add_tag_ids: list[str],
        remove_tag_ids: list[str],
        dry_run: bool,
        actor_id: Optional[str],
        apply_settings: bool = False,
        gates=None,
    ) -> dict:
        add = self._require_tags(add_tag_ids)
        remove = self._require_tags(remove_tag_ids)
        ids = list(dict.fromkeys(contact_ids))
        contacts = {
            str(c.id): c
            for c in self.db.query(RespondContact).filter(RespondContact.id.in_(ids)).all()
        }
        held = tags_by_contact(self.db, contacts.keys())
        all_names = {t: row.name for t, row in {**add, **remove}.items()}

        results: list[dict] = []
        inserts: list[dict] = []
        deletes: list[tuple[str, str]] = []
        counts = {"changed": 0, "unchanged": 0, "failed": 0}
        for cid in ids:
            contact = contacts.get(cid)
            if contact is None:
                counts["failed"] += 1
                results.append(
                    {"contact_id": cid, "label": cid, "status": "failed", "tags_before": [],
                     "tags_after": [], "error": "Contact not found"}
                )
                continue
            current = {t["id"]: t["name"] for t in held.get(cid, [])}
            after = {k: v for k, v in current.items() if k not in remove}
            for tag_id in add:
                after.setdefault(tag_id, all_names[tag_id])
            to_add = [t for t in add if t not in current]
            to_remove = [t for t in remove if t in current]
            changed = bool(to_add or to_remove)
            counts["changed" if changed else "unchanged"] += 1
            inserts += [{"contact_id": cid, "tag_id": t, "created_by": actor_id} for t in to_add]
            deletes += [(cid, t) for t in to_remove]
            results.append(
                {
                    "contact_id": cid,
                    "label": contact.name or contact.phone_number or cid,
                    "status": "changed" if changed else "unchanged",
                    "tags_before": sorted(current.values()),
                    "tags_after": sorted(after.values()),
                    "error": None,
                }
            )

        def write_tags() -> None:
            for cid, tag_id in deletes:
                self.db.execute(
                    delete(respond_contact_tags).where(
                        and_(respond_contact_tags.c.contact_id == cid, respond_contact_tags.c.tag_id == tag_id)
                    )
                )
            if inserts:
                self.db.execute(insert(respond_contact_tags), inserts)
            self.db.flush()

        if apply_settings and add and gates is not None:
            # The settings of the tags ADDED here are applied after the tag change; a preview
            # runs the same change inside a savepoint it then rolls back (preview equals apply).
            from app.services import contact_tag_apply

            savepoint = self.db.begin_nested()
            try:
                write_tags()
                applied = contact_tag_apply.apply_settings(
                    self.db,
                    [r["contact_id"] for r in results if r["status"] != "failed"],
                    dry_run=dry_run,
                    actor_id=actor_id,
                    can=gates[0],
                    is_superadmin=gates[1],
                    only_tags=set(add),
                )
            except Exception:
                savepoint.rollback()
                raise
            if dry_run:
                savepoint.rollback()
            else:
                savepoint.commit()
                self.db.commit()
            by_contact = {r["contact_id"]: r for r in applied["results"]}
            for r in results:
                r["settings"] = by_contact.get(r["contact_id"])
        elif not dry_run and (inserts or deletes):
            write_tags()
            self.db.commit()
        return {"dry_run": dry_run, "results": results, "counts": counts}
