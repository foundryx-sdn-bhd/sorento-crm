"""Contact service for business logic."""
from sqlalchemy.orm import Session
from sqlalchemy import exists, func, or_
from typing import Optional
import logging
import json
from fastapi import HTTPException
from app.models.access import RespondContact
from app.models.company import Company, RespondContactCompany
from app.schemas.user import RespondContactCreate, RespondContactUpdate, RespondContactResponse, ContactAgentAccessCreate
from app.services.error_handler import handle_not_found, handle_conflict, handle_validation_error
from app.services.integration_service import RespondClient, IntegrationLogService
from app.schemas.integration import IntegrationLogCreate
from app.schemas.common import ListResponse, PaginationResponse

logger = logging.getLogger(__name__)


def _respond_contact_root_dict(payload: dict) -> dict:
    """Pick the object that holds Respond contact fields (not assignee)."""
    if not isinstance(payload, dict):
        return {}
    for key in ("contact", "data"):
        inner = payload.get(key)
        if isinstance(inner, dict) and (
            "phone" in inner
            or "firstName" in inner
            or "lastName" in inner
            or "first_name" in inner
            or "last_name" in inner
        ):
            return inner
    return payload


def parse_respond_contact_payload(payload: dict) -> dict:
    """
    Extract contact fields from Respond.io GET /v2/contact/... JSON.
    Does not use assignee (that is the agent, not the contact).
    """
    root = _respond_contact_root_dict(payload)
    out: dict = {}

    fn_key = "firstName" if "firstName" in root else ("first_name" if "first_name" in root else None)
    ln_key = "lastName" if "lastName" in root else ("last_name" if "last_name" in root else None)

    if fn_key:
        raw = root.get(fn_key)
        if raw is None:
            out["first_name"] = None
        elif isinstance(raw, str):
            out["first_name"] = raw.strip() or None
        else:
            out["first_name"] = str(raw).strip() or None
    if ln_key:
        raw = root.get(ln_key)
        if raw is None:
            out["last_name"] = None
        elif isinstance(raw, str):
            out["last_name"] = raw.strip() or None
        else:
            out["last_name"] = str(raw).strip() or None

    name = root.get("name") or root.get("displayName")
    if isinstance(name, str):
        name = name.strip() or None
    if name is not None:
        out["name"] = name
    elif "first_name" in out or "last_name" in out:
        fn = out.get("first_name") or ""
        ln = out.get("last_name") or ""
        combined = f"{fn} {ln}".strip()
        if combined:
            out["name"] = combined

    rid = root.get("id")
    if rid is not None:
        s = str(rid).strip()
        if s:
            out["respond_io_id"] = s

    return out


class ContactService:
    """Service for respond contact operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_contact(self, contact_id: str) -> RespondContact:
        """Get a contact by ID."""
        contact = self.db.query(RespondContact).filter(RespondContact.id == contact_id).first()
        if not contact:
            raise handle_not_found("Respond Contact", contact_id)
        return contact
    
    def get_contact_by_phone(self, phone_number: str) -> Optional[RespondContact]:
        """Get a contact by phone number."""
        return self.db.query(RespondContact).filter(RespondContact.phone_number == phone_number).first()
    
    def list_contacts(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        own_memory_level_only: bool = False,
        include_linked_users: bool = False,
        no_customers_only: bool = False,
        access_filters: Optional[dict] = None,
    ):
        """List contacts with pagination and filtering.

        ``access_filters`` (CONTACT-BULK-ACCESS, UAC A2.3): any of ``access_type``, ``tier``,
        ``cost``, ``escalation``, ``packing_list``, ``stock``, ``customer_id``,
        ``access_differs_from``; absent or None = no filter. See ``_apply_access_filters``.

        ``include_linked_users`` (S3 1.7): the caller holds `users.view`, so
        each row's `linked_user_id`/`linked_user_name` are filled from ONE
        batched query for the whole page - never one query per row.
        """
        q = self.db.query(RespondContact)
        if own_memory_level_only:
            q = q.filter(RespondContact.chatbot_memory_level.isnot(None))
        if no_customers_only:
            from app.models.access import RespondContactCustomer

            from app.models.base import get_company_scope
            from app.services.company_scope import build_company_predicate

            link_exists = exists().where(RespondContactCustomer.contact_id == RespondContact.id)
            # Only links the caller can see count; None (all companies) adds nothing,
            # an empty scope resolves to a false predicate (fail closed).
            predicate = build_company_predicate(RespondContactCustomer, get_company_scope(self.db))
            if predicate is not None:
                link_exists = link_exists.where(predicate)
            q = q.filter(~link_exists)

        if query:
            like = f"%{query}%"
            q = q.filter(
                or_(
                    RespondContact.phone_number.ilike(like),
                    RespondContact.name.ilike(like),
                    RespondContact.first_name.ilike(like),
                    RespondContact.last_name.ilike(like),
                )
            )
        if access_filters:
            q = self._apply_access_filters(q, access_filters)
        
        total = q.count()
        
        # Handle sorting
        sort_map = {
            "phone_number": RespondContact.phone_number,
            "name": RespondContact.name,
            "first_name": RespondContact.first_name,
            "last_name": RespondContact.last_name,
            "created_at": RespondContact.created_at,
            "updated_at": RespondContact.updated_at,
        }
        sort_attr = sort_map.get(sort_field, RespondContact.created_at)
        if sort_dir == "desc":
            sort_attr = sort_attr.desc()
        else:
            sort_attr = sort_attr.asc()
        
        contacts = q.order_by(sort_attr).offset((page - 1) * limit).limit(limit).all()

        linked_map: dict[str, dict] = {}
        if include_linked_users and contacts:
            from app.services.user_contact_link import linked_user_map

            linked_map = linked_user_map(self.db, [str(c.id) for c in contacts])

        from app.services.contact_customer_service import customer_codes_by_contact

        codes_map = customer_codes_by_contact(self.db, [str(c.id) for c in contacts])
        cost_ids = self._cost_visible_ids([str(c.id) for c in contacts])

        from app.services.contact_brand_scope import brand_refs_by_contact

        brands_map = brand_refs_by_contact(self.db, [str(c.id) for c in contacts])

        # Validate and convert contacts to response models
        # Explicitly convert UUID to string to ensure Pydantic validation works
        contact_responses = []
        for contact in contacts:
            try:
                data = self.contact_to_response_dict(contact)
                linked = linked_map.get(str(contact.id))
                data["linked_user_id"] = linked["id"] if linked else None
                data["linked_user_name"] = linked["name"] if linked else None
                data["customer_codes"] = codes_map.get(str(contact.id), [])
                data["brands"] = brands_map.get(str(contact.id), [])
                data["cost_visible"] = str(contact.id) in cost_ids
                contact_responses.append(RespondContactResponse.model_validate(data))
            except Exception as e:
                logger.error(f"Error validating contact {contact.id}: {str(e)}", exc_info=True)
                raise
        
        return ListResponse(
            data=contact_responses,
            pagination=PaginationResponse(total=total, page=page, limit=limit),
            empty=total == 0
        )
    
    COST_REVEAL_KEY = "purchase_orders.cost"

    def _cost_visible_ids(self, contact_ids: list[str]) -> set[str]:
        """The contacts among ``contact_ids`` holding the cost reveal, in one query."""
        if not contact_ids:
            return set()
        from app.models.access import ContactFieldReveal

        return {
            str(cid)
            for (cid,) in self.db.query(ContactFieldReveal.respond_contact_id).filter(
                ContactFieldReveal.respond_contact_id.in_(contact_ids),
                ContactFieldReveal.field_key == self.COST_REVEAL_KEY,
                ContactFieldReveal.granted.is_(True),
            )
        }

    def _apply_access_filters(self, q, f: dict):
        """UAC A2.3-A2.6: each filter narrows; together they AND. `yes|no` for the switches."""
        from app.models.access import (
            ContactFieldReveal,
            RespondContactCustomer,
            respond_contact_access_types as pivot,
        )

        def yes(name: str) -> Optional[bool]:
            value = f.get(name)
            return None if not value else value == "yes"

        if f.get("access_type"):
            q = q.filter(
                exists().where(
                    pivot.c.contact_id == RespondContact.id,
                    pivot.c.access_type_code == f["access_type"],
                )
            )
        tier = f.get("tier")
        if tier:
            tier_value = RespondContact.chatbot_profile["tier"].astext
            if tier == "none":
                q = q.filter(or_(tier_value.is_(None), tier_value == ""))
            else:
                q = q.filter(tier_value == tier)
        cost = yes("cost")
        if cost is not None:
            holds = exists().where(
                ContactFieldReveal.respond_contact_id == RespondContact.id,
                ContactFieldReveal.field_key == self.COST_REVEAL_KEY,
                ContactFieldReveal.granted.is_(True),
            )
            q = q.filter(holds if cost else ~holds)
        for name, column in (
            ("escalation", RespondContact.escalation_allowed),
            ("packing_list", RespondContact.packing_list_allowed),
            ("stock", RespondContact.chatbot_stock_allowed),
        ):
            value = yes(name)
            if value is not None:
                q = q.filter(column.is_(value))
        if f.get("customer_id"):
            from app.models.base import get_company_scope
            from app.services.company_scope import build_company_predicate

            link = exists().where(
                RespondContactCustomer.contact_id == RespondContact.id,
                RespondContactCustomer.customer_id == f["customer_id"],
            )
            # Same rule as `customers=none`: only links the caller can see count.
            predicate = build_company_predicate(RespondContactCustomer, get_company_scope(self.db))
            if predicate is not None:
                link = link.where(predicate)
            q = q.filter(link)
        if f.get("access_differs_from"):
            from app.services.contact_access_copy_service import contacts_differing_from

            from app.services.contact_access_copy_service import MAX_DIFF_CANDIDATES
            from app.services.error_handler import handle_unprocessable

            source = self.get_contact(f["access_differs_from"])
            if q.count() > MAX_DIFF_CANDIDATES:
                raise handle_unprocessable(
                    f"'Access differs from' compares at most {MAX_DIFF_CANDIDATES} contacts; "
                    "narrow the list with another filter or a search first."
                )
            ids = contacts_differing_from(self.db, source, q.all())
            q = q.filter(RespondContact.id.in_(ids))
        return q

    def assign_default_agents_to_contact(self, contact: RespondContact) -> None:
        """Assign all agents with assign_to_new_internal_contacts=True to this contact. Idempotent per agent (skips if access already exists).
        Sets valid_from to start of current year and valid_to to end of current year (for external-API-created contacts)."""
        from datetime import datetime
        from app.services.user_service import AccessAgentService

        now = datetime.utcnow()
        valid_from = datetime(now.year, 1, 1, 0, 0, 0)
        valid_to = datetime(now.year, 12, 31, 23, 59, 59)

        try:
            access_agent_service = AccessAgentService(self.db)
            default_agents = access_agent_service.list_agents_assign_to_new_internal_contacts()
            contact_name = (str(getattr(contact, "name", None)) if getattr(contact, "name", None) is not None else "") or ""
            for agent in default_agents:
                try:
                    access_agent_service.create_contact_access(
                        str(agent.id),
                        ContactAgentAccessCreate(
                            respond_contact_phone=str(contact.phone_number),
                            respond_contact_name=contact_name,
                            agent_id=str(agent.id),
                            is_allowed=True,
                            valid_from=valid_from,
                            valid_to=valid_to,
                        ),
                    )
                except Exception as assign_err:
                    # Already exists or other error - log and continue
                    logger.warning(
                        "Assign default agent %s to new contact %s failed: %s",
                        getattr(agent, "code", agent.id),
                        contact.phone_number,
                        assign_err,
                    )
        except Exception as e:
            logger.warning("Assign default agents to new contact failed: %s", e)

    def create_contact(self, contact_data: RespondContactCreate) -> RespondContact:
        """Create a new contact and assign default access agents (assign_to_new_internal_contacts=True).

        Access types are stored in the ``respond_contact_access_types`` pivot. When
        ``access_type_codes`` is provided, the catalog is validated and the contact
        is linked to those types.

        If no ``workspace_id`` is provided, falls back to the tenant's default
        Respond.io workspace (``respond_workspaces.is_default = true``) so that
        scheduled-task and individual syncs land contacts under the configured
        default automatically.
        """
        # Check if contact with same phone number already exists
        existing = self.get_contact_by_phone(contact_data.phone_number)
        if existing:
            raise handle_conflict("Contact with this phone number already exists.")
        data = contact_data.model_dump()
        codes = data.pop("access_type_codes", None)
        if not data.get("workspace_id"):
            from app.services.respond_workspace_service import RespondWorkspaceService
            default_ws = RespondWorkspaceService(self.db).get_default()
            if default_ws is not None:
                data["workspace_id"] = str(default_ws.id)
        contact = RespondContact(**data)
        self.db.add(contact)
        self.db.flush()
        if codes:
            from app.services.contact_access_type_service import ContactAccessTypeService
            ContactAccessTypeService(self.db).set_contact_access_codes(str(contact.id), codes)
        self.db.commit()
        self.db.refresh(contact)
        self.assign_default_agents_to_contact(contact)
        return contact

    def update_contact(self, contact_id: str, contact_data: RespondContactUpdate) -> RespondContact:
        """Update a contact. When ``access_type_codes`` is supplied, the M2M assignment is replaced."""
        contact = self.get_contact(contact_id)

        update_data = contact_data.model_dump(exclude_unset=True)
        codes_present = "access_type_codes" in update_data
        codes = update_data.pop("access_type_codes", None)

        # Check phone number uniqueness if being updated
        if 'phone_number' in update_data and update_data['phone_number'] != contact.phone_number:
            existing = self.get_contact_by_phone(update_data['phone_number'])
            if existing is not None and str(existing.id) != contact_id:
                raise handle_conflict("Contact with this phone number already exists.")

        # Reviewer pass at d89110c0 (S8): `facts` has its own guarded write path
        # (`chatbot/facts/{key}`); a generic profile write never touches it.
        chatbot_profile = update_data.pop("chatbot_profile", None)
        if chatbot_profile is not None:
            self.write_chatbot_profile_keeping_facts(contact.id, chatbot_profile)

        for key, value in update_data.items():
            setattr(contact, key, value)

        if codes_present:
            from app.services.contact_access_type_service import ContactAccessTypeService
            ContactAccessTypeService(self.db).set_contact_access_codes(contact_id, codes or [])

        self.db.commit()
        self.db.refresh(contact)
        return contact

    def list_contact_companies(self, contact_id: str) -> list[dict]:
        """Return the companies granted to a contact as ``[{id,name,code}]``."""
        self.get_contact(contact_id)
        rows = (
            self.db.query(Company)
            .join(RespondContactCompany, RespondContactCompany.company_id == Company.id)
            .filter(RespondContactCompany.respond_contact_id == contact_id)
            .order_by(Company.name.asc())
            .all()
        )
        return [{"id": str(c.id), "name": c.name, "code": c.code} for c in rows]

    def set_contact_companies(self, contact_id: str, company_ids: list[str]) -> dict:
        """Replace a contact's company grants with the given ids (unknown ids skipped)."""
        self.get_contact(contact_id)
        self.db.query(RespondContactCompany).filter(
            RespondContactCompany.respond_contact_id == contact_id
        ).delete(synchronize_session=False)
        self.db.flush()
        granted: list[str] = []
        for cid in company_ids or []:
            if cid in granted:
                continue
            if self.db.query(Company).filter(Company.id == cid).first():
                self.db.add(RespondContactCompany(company_id=cid, respond_contact_id=contact_id))
                granted.append(cid)
        self.db.commit()
        return {"message": "Contact companies updated successfully"}

    def _respond_id_belongs_uniquely_to(self, contact: RespondContact) -> bool:
        """`respond_io_id` is unique WITHIN a Respond.io workspace only, never
        globally (security review 26 Sep 2026, B1) - a shared id means the
        conversation_frame rows keyed by it could belong to a NAMESAKE in another
        workspace. `True` only when exactly one `respond_contacts` row carries it."""
        if not contact.respond_io_id:
            return False
        count = (
            self.db.query(RespondContact)
            .filter(RespondContact.respond_io_id == contact.respond_io_id)
            .count()
        )
        return count == 1

    def delete_contact(self, contact_id: str) -> None:
        """Delete a respond contact and all contact - agent linkages. Related contact_agent_access rows are deleted; conversation_sla_tracking.respond_contact_id is SET NULL."""
        from app.models.access import ContactAgentAccess
        from app.models.conversation_frame import ConversationFrame

        contact = self.get_contact(contact_id)
        # Delete contact - agent access linkages first so the contact can be removed
        self.db.query(ContactAgentAccess).filter(
            ContactAgentAccess.respond_contact_id == contact_id
        ).delete(synchronize_session=False)
        # Chatbot memory lane A (contract section 3): a deleted contact's episodes go
        # with it. Keyed by `respond_io_id` - the episode store's own contact key,
        # never the internal `respond_contacts.id`. Security review 26 Sep 2026 (B1):
        # a `respond_io_id` shared with another workspace's contact is refused rather
        # than deleted, so a namesake's frames never get swept up by this delete.
        if contact.respond_io_id:
            if self._respond_id_belongs_uniquely_to(contact):
                self.db.query(ConversationFrame).filter(
                    ConversationFrame.contact_respond_id == contact.respond_io_id
                ).delete(synchronize_session=False)
            else:
                logger.warning(
                    "chatbot: respond_io_id %s is shared by more than one contact; "
                    "skipping conversation_frame delete for contact %s",
                    contact.respond_io_id,
                    contact_id,
                )
        self.db.delete(contact)
        self.db.commit()

    def bulk_delete_contacts(self, contact_ids: list[str]) -> dict:
        """Delete multiple contacts by id. Returns deleted_count and message."""
        if not contact_ids:
            return {"deleted_count": 0, "message": "No contacts to delete."}
        from app.models.access import ContactAgentAccess
        from app.models.conversation_frame import ConversationFrame
        deleted = 0
        for contact_id in contact_ids:
            contact = self.db.query(RespondContact).filter(RespondContact.id == contact_id).first()
            if contact:
                self.db.query(ContactAgentAccess).filter(
                    ContactAgentAccess.respond_contact_id == contact_id
                ).delete(synchronize_session=False)
                # Security review 26 Sep 2026 (B1): same refusal as `delete_contact`
                # - never touch frames by a `respond_io_id` a namesake also holds.
                if contact.respond_io_id:
                    if self._respond_id_belongs_uniquely_to(contact):
                        self.db.query(ConversationFrame).filter(
                            ConversationFrame.contact_respond_id == contact.respond_io_id
                        ).delete(synchronize_session=False)
                    else:
                        logger.warning(
                            "chatbot: respond_io_id %s is shared by more than one "
                            "contact; skipping conversation_frame delete for "
                            "contact %s",
                            contact.respond_io_id,
                            contact_id,
                        )
                self.db.delete(contact)
                deleted += 1
        self.db.commit()
        return {"deleted_count": deleted, "message": f"Deleted {deleted} contact(s)."}

    # --------------------------------------------------------------------- #
    # Chatbot memory lane A (contract section 5) - the contact's memory/facts
    # surface. Reaches into `app.services.chatbot.turn.profile_facts` for the
    # vocabulary, precedence and lock rules the engine's own writers already use
    # (`test_import_boundary.py::ALLOWED` names this file as the one non-chatbot
    # doorway, the same way it already names `app/api/v1/system/chatbot.py`).
    # --------------------------------------------------------------------- #

    def write_chatbot_profile_keeping_facts(self, contact_pk: str, profile: dict) -> None:
        """Replace `chatbot_profile` with `profile`, except `facts`: whatever `facts`
        the body carries is dropped, and whatever is stored at write time is kept.

        One UPDATE reading the stored facts inside the statement, so a fact a turn
        commits between the caller's read and this write is not lost (reviewer pass
        at d89110c0, S7; AC-MEM040). Not committed here."""
        from sqlalchemy import text

        body = {k: v for k, v in dict(profile).items() if k != "facts"}
        self.db.execute(
            text(
                "UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) || CASE "
                "WHEN chatbot_profile ? 'facts' THEN jsonb_build_object('facts', chatbot_profile->'facts') "
                "ELSE '{}'::jsonb END WHERE id = :i"
            ),
            {"p": json.dumps(body), "i": str(contact_pk)},
        )
        self.db.expire_all()

    def get_chatbot_memory(self, contact_id: str, *, include_episodes: bool) -> dict:
        from app.models.user import SystemSetting
        from app.services.chatbot.turn import memory as memory_mod
        from app.services.chatbot.turn import profile_facts

        contact = self.get_contact(contact_id)
        system_setting = self.db.query(SystemSetting).first()
        system_memory = (getattr(system_setting, "chatbot_memory", None) or {}) if system_setting else {}
        own_level = getattr(contact, "chatbot_memory_level", None)
        effective = memory_mod.resolve_level(own_level, system_memory)

        merged = profile_facts.merged_facts_for_display(self.db, contact)
        facts_out = [
            profile_facts.fact_for_display(key, entry)
            for key in profile_facts.VOCABULARY
            for entry in (next((f for f in merged if f.get("key") == key), None),)
            # A tombstone (`value: null`) is bookkeeping, not a fact (S10).
            if entry is not None and entry.get("value") is not None
        ]
        vocabulary_out = [profile_facts.vocabulary_entry(key) for key in profile_facts.VOCABULARY]

        return {
            "level": {
                "own": own_level,
                "effective": effective,
                # Reviewer pass at d89110c0 (S6): with the switch off, the default
                # a contact follows is off, whatever `default_level` is stored.
                "system_default": (
                    (system_memory.get("default_level") or "full") if system_memory.get("enabled") else "off"
                ),
            },
            "facts": facts_out,
            "vocabulary": vocabulary_out,
            # Security review 26 Sep 2026 (S3): free-text conversation summaries are
            # gated behind `system.chat_history.view` ON TOP of the route's own view
            # permission - `None` (never an empty list, which would read as "no
            # conversations" rather than "not shown to you") when the caller lacks it.
            "episodes": self._chatbot_episodes_summary(contact) if include_episodes else None,
            "open_orders": self._chatbot_open_orders(contact),
        }

    #: N3 (security review 26 Sep 2026): the newest N closed frames are what a
    #: staff card ever needs to show - bounded so a long-lived contact's full
    #: episode history is never loaded row-by-row on every memory-card read.
    _EPISODE_ROWS_LIMIT = 10
    #: N3: an "open turns" query has no natural cap otherwise (a contact that
    #: never closes a topic would load every turn it ever sent).
    _OPEN_TURNS_LIMIT = 50

    def _chatbot_episodes_summary(self, contact: RespondContact) -> dict:
        from app.models.conversation_frame import ConversationFrame
        from app.services.chatbot.turn.episode_digest import readable_summary, topic_label
        from app.services.chatbot.turn.memory import KEEP_EPISODES

        empty = {
            "kept": 0,
            "console_kept": 0,
            "limit": KEEP_EPISODES,
            "current": None,
            "console_current": None,
            "rows": [],
        }
        respond_id = contact.respond_io_id
        if not respond_id:
            return empty
        # Security review 26 Sep 2026 (B1): a `respond_io_id` shared by more than
        # one contact is not this contact's alone to read - refuse rather than
        # showing a namesake's conversations.
        if not self._respond_id_belongs_uniquely_to(contact):
            logger.warning(
                "chatbot: respond_io_id %s is shared by more than one contact; "
                "refusing to read conversation_frame rows for contact %s",
                respond_id,
                contact.id,
            )
            return empty
        # Fix lane round 3 (R1): both worlds. A console turn writes its own test
        # frames (D14's third exception, Q15), and the owner hand-tests from the
        # console, so the staff screen shows them too, each row marked `console`.
        # The bot itself still never reads across worlds (`_memory_intake`).
        kept = (
            self.db.query(ConversationFrame)
            .filter(
                ConversationFrame.contact_respond_id == respond_id,
                ConversationFrame.is_test.is_(False),
            )
            .count()
        )
        console_kept = (
            self.db.query(ConversationFrame)
            .filter(
                ConversationFrame.contact_respond_id == respond_id,
                ConversationFrame.is_test.is_(True),
            )
            .count()
        )
        frames = (
            self.db.query(ConversationFrame)
            .filter(ConversationFrame.contact_respond_id == respond_id)
            .order_by(ConversationFrame.last_activity_at.desc())
            .limit(self._EPISODE_ROWS_LIMIT)
            .all()
        )
        rows = [
            {
                "id": f.id,
                "date": f.last_activity_at.isoformat() if f.last_activity_at else None,
                "domains": [f.domain] if f.domain else [],
                # The recall reply's own Topic word (`Stock`, `Incoming stock`).
                "topic": topic_label(f.domain),
                # Fix round 6: never the old bracket-tag chain, even before the
                # backfill rewrites it (the recall reply reads the same way).
                "summary": readable_summary(f.summary, f.domain, f.entities),
                "turn_count": len(f.turn_ids or []),
                "close_reason": f.close_reason,
                "first_turn_id": (f.turn_ids or [None])[0],
                "console": bool(f.is_test),
            }
            for f in frames
        ]
        return {
            "kept": kept,
            "console_kept": console_kept,
            "limit": KEEP_EPISODES,
            "current": self._chatbot_open_conversation(respond_id, is_test=False),
            "console_current": self._chatbot_open_conversation(respond_id, is_test=True),
            "rows": rows,
        }

    def _chatbot_open_conversation(self, respond_id: str, *, is_test: bool) -> dict | None:
        """The open conversation of one world: this contact's turns in no frame yet
        (`memory.open_turns_newest`, the same reading the switch detector uses). R2
        (fix lane round 3): `domains` is the current domain, the newest one a turn
        planned, never an empty list while one exists."""
        from app.models.chatbot_turn import ChatbotTurn
        from app.services.chatbot.turn import memory as memory_mod
        from app.services.chatbot.turn.episode_digest import digest, topic_domain, topic_label

        query = memory_mod._open_turns_query(
            self.db, contact_respond_id=respond_id, is_test=is_test, exclude_turn_id=None
        )
        turn_count = query.count()
        if not turn_count:
            return None
        first = query.order_by(ChatbotTurn.created_at.asc()).first()
        # N3: bounded, the newest turns only (their `trace` is the heavy column).
        newest = memory_mod.open_turns_newest(
            self.db, contact_respond_id=respond_id, is_test=is_test, limit=self._OPEN_TURNS_LIMIT
        )
        turn_dicts = [memory_mod._turn_to_digest_dict(r) for r in reversed(newest)]
        domain = next(
            (d for d in (topic_domain(t) for t in reversed(turn_dicts)) if d),
            None,
        )
        return {
            "turn_count": turn_count,
            "first_turn_id": first.id if first is not None else None,
            "started_at": first.created_at.isoformat() if first is not None and first.created_at else None,
            "summary": digest(turn_dicts)["summary"] if turn_dicts else "",
            "domains": [domain] if domain else [],
            "topic": topic_label(domain),
        }

    def _chatbot_open_orders(self, contact: RespondContact) -> dict:
        from app.models.access import RespondContactCustomer
        from app.models.order import Customer, SalesOrder

        primary = (
            self.db.query(Customer)
            .join(
                RespondContactCustomer,
                RespondContactCustomer.customer_id == Customer.id,
            )
            .filter(
                RespondContactCustomer.contact_id == contact.id,
                RespondContactCustomer.is_primary.is_(True),
            )
            .first()
        )
        if primary is None:
            return {"customer_name": None, "rows": []}
        orders = (
            self.db.query(SalesOrder)
            .filter(SalesOrder.customer_id == primary.id, SalesOrder.status.notin_(("closed", "cancelled")))
            .order_by(SalesOrder.order_date.desc().nullslast())
            .limit(5)
            .all()
        )
        rows = [
            {
                "document": order.so_number,
                "kind": "sales_order",
                "status": (order.status or "open").replace("_", " ").title(),
                "summary": "",
                "date": order.order_date.isoformat() if order.order_date else None,
                "href": f"/scm/sales-orders/{order.id}",
            }
            for order in orders
        ]
        return {"customer_name": primary.customer_name, "rows": rows}

    def set_contact_fact(self, contact_id: str, key: str, value, *, user_id: str, include_episodes: bool) -> dict:
        from app.services.chatbot.turn import profile_facts
        from app.services.error_handler import handle_unprocessable

        self.get_contact(contact_id)
        entry = profile_facts.set_staff_fact(self.db, contact_id, key, value, user_id=user_id)
        if entry is None:
            raise handle_unprocessable(f"{key!r} is not an editable fact, or the value is invalid.")
        return self.get_chatbot_memory(contact_id, include_episodes=include_episodes)

    def delete_contact_fact(self, contact_id: str, key: str) -> None:
        """N1 (security review 26 Sep 2026): 422 on an unknown key, the same as the
        PUT route - never a silent no-op or a 500 for a caller's typo."""
        from app.services.chatbot.turn import profile_facts
        from app.services.error_handler import handle_unprocessable

        self.get_contact(contact_id)
        spec = profile_facts.VOCABULARY.get(key)
        # N5: a CRM-only key has nothing stored to delete.
        if spec is None or not spec.allow_staff:
            raise handle_unprocessable(f"{key!r} is not an editable fact.")
        profile_facts.delete_fact(self.db, contact_id, key)

    @staticmethod
    def contact_to_response_dict(contact: RespondContact, db: Optional[Session] = None) -> dict:
        ws = getattr(contact, "workspace", None)
        access_types = list(getattr(contact, "access_types", []) or [])
        chatbot_profile = dict(getattr(contact, "chatbot_profile", None) or {})
        brands: list = []
        if db is not None:
            from app.services.contact_brand_scope import brand_refs_by_contact

            brands = brand_refs_by_contact(db, [str(contact.id)]).get(str(contact.id), [])
            # AC-MEM041: both dict builders carry facts, the live CRM ones (never
            # stored) merged in the same way the memory GET does. Skipped entirely
            # when there is nothing to add - a contact with no stored facts and no
            # CRM link keeps the exact `{}` `chatbot_profile` pre-lane-A callers
            # already assert on, rather than growing an always-empty `facts: []`.
            from app.services.chatbot.turn import profile_facts

            merged = profile_facts.merged_facts_for_display(db, contact)
            if merged:
                chatbot_profile["facts"] = merged
        return {
            "id": str(contact.id),
            "phone_number": contact.phone_number,
            "name": contact.name,
            "first_name": getattr(contact, "first_name", None),
            "last_name": getattr(contact, "last_name", None),
            "respond_io_id": getattr(contact, "respond_io_id", None),
            "workspace_id": str(contact.workspace_id) if getattr(contact, "workspace_id", None) else None,
            "workspace_name": getattr(ws, "name", None) if ws is not None else None,
            "workspace_space_id": getattr(ws, "space_id", None) if ws is not None else None,
            # Must be listed explicitly: this dict is built by hand, so a column the
            # schema inherits still never reaches the FE unless it appears here.
            "requires_registered_project": bool(
                getattr(contact, "requires_registered_project", False)
            ),
            "access_type_codes": [str(a.code) for a in access_types],
            "access_types": [
                {"code": str(a.code), "name": a.name, "sort_order": a.sort_order}
                for a in access_types
            ],
            # The outbound kill switch, so the contacts grid can show who is
            # silenced. A manual dict builder drops anything it does not list.
            "outbound_enabled": bool(getattr(contact, "outbound_enabled", True)),
            # Chatbot turn re-architecture (AC-1503) - same rule as every field above.
            "chatbot_profile": chatbot_profile,
            # CONTACT-BULK-ACCESS (UAC A2.1): flattened for the list's Tier column/filter.
            "chatbot_tier": (chatbot_profile.get("tier") or None),
            # Chatbot memory lane A (contract section 5): null = follow the system
            # default. Must be listed explicitly, same rule as every field above.
            "chatbot_memory_level": getattr(contact, "chatbot_memory_level", None),
            # S6: the stock allowance, default ON - a row without the attribute is allowed.
            "chatbot_stock_allowed": bool(getattr(contact, "chatbot_stock_allowed", True)),
            # Chatbot stock ask v2 S2 (PLAN-chatbot-stock-ask-v2-24sep.md, R7): both
            # default OFF, unlike chatbot_stock_allowed above.
            "notify_salesman": bool(getattr(contact, "notify_salesman", False)),
            "packing_list_allowed": bool(getattr(contact, "packing_list_allowed", False)),
            # Issue #1328: default ON, like chatbot_stock_allowed.
            "chatbot_eta_offset_applied": bool(getattr(contact, "chatbot_eta_offset_applied", True)),
            # ESCALATION-CONTROL: the one per-contact switch, default ON - a row without
            # the attribute is allowed. Listed by hand, like every field above.
            "escalation_allowed": getattr(contact, "escalation_allowed", True) is not False,
            # CONTACT-BRAND-SCOPE: listed by hand like every field above. The list endpoint
            # fills it for a whole page in one query; here, one contact when a session is given.
            "brands": brands,
            "created_at": contact.created_at,
            "updated_at": contact.updated_at,
            "created_by": contact.created_by,
        }

    def sync_contact_name(self, contact_id: str) -> RespondContact:
        """Sync contact name, first/last name, and respond_io_id from Respond.io API.

        Access types are CRM-managed (M2M via respond_contact_access_types) and are
        no longer touched by this sync.
        """
        contact = self.get_contact(contact_id)
        log_service = IntegrationLogService(self.db)

        # Create integration log for the sync operation
        request_payload = {
            "contact_id": contact_id,
            "phone_number": contact.phone_number,
            "action": "sync_contact_name"
        }

        try:
            client = RespondClient()
            # Use phone: prefix format
            payload = client.get_contact_by_phone(str(contact.phone_number))
            parsed = parse_respond_contact_payload(payload)

            name = parsed.get("name")

            if parsed.get("first_name") is not None:
                contact.first_name = parsed["first_name"]
            if parsed.get("last_name") is not None:
                contact.last_name = parsed["last_name"]
            if name is not None:
                contact.name = name
            if parsed.get("respond_io_id") is not None:
                contact.respond_io_id = parsed["respond_io_id"]

            workspace_assigned = False
            if not contact.workspace_id:
                from app.services.respond_workspace_service import RespondWorkspaceService
                default_ws = RespondWorkspaceService(self.db).get_default()
                if default_ws is not None:
                    contact.workspace_id = str(default_ws.id)
                    workspace_assigned = True

            updated = bool(
                parsed.get("first_name") is not None
                or parsed.get("last_name") is not None
                or name is not None
                or parsed.get("respond_io_id") is not None
                or workspace_assigned
            )

            if updated:
                self.db.commit()
                self.db.refresh(contact)
                logger.info(
                    "Synced contact %s: name=%s first=%s last=%s respond_io_id=%s",
                    contact.phone_number,
                    contact.name,
                    contact.first_name,
                    contact.last_name,
                    contact.respond_io_id,
                )
                
                # Log successful sync
                log_service.create_integration_log(
                    IntegrationLogCreate(
                        integration_channel="respond_io",
                        business_table="respond_contacts",
                        business_id=contact_id,
                        direction="outbound",
                        endpoint=f"/v2/contact/phone:{contact.phone_number}",
                        http_method="GET",
                        status="success",
                        response_payload=json.dumps(payload, indent=2)
                    ),
                    request_payload_dict=request_payload
                )
            else:
                error_msg = (
                    f"No syncable contact fields in Respond.io response for {contact.phone_number}"
                )
                logger.warning(error_msg)
                
                # Log warning - no name found
                log_service.create_integration_log(
                    IntegrationLogCreate(
                        integration_channel="respond_io",
                        business_table="respond_contacts",
                        business_id=contact_id,
                        direction="outbound",
                        endpoint=f"/v2/contact/phone:{contact.phone_number}",
                        http_method="GET",
                        status="failed",
                        error_message=error_msg,
                        response_payload=json.dumps(payload, indent=2)
                    ),
                    request_payload_dict=request_payload
                )
            
            return contact
        except ValueError as e:
            # API key not configured
            error_msg = str(e)
            logger.error(f"Error syncing contact name for {contact_id}: {error_msg}", exc_info=True)
            
            # Log the error
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="respond_contacts",
                    business_id=contact_id,
                    direction="outbound",
                    endpoint=f"/v2/contact/phone:{contact.phone_number}",
                    http_method="GET",
                    status="failed",
                    error_code="CONFIGURATION_ERROR",
                    error_message=error_msg
                ),
                request_payload_dict=request_payload
            )
            raise
        except Exception as e:
            # Other errors (API errors, network errors, etc.)
            import httpx
            
            error_msg = str(e)
            error_code = "API_ERROR"
            status_code = None
            
            # Handle httpx exceptions
            response_payload = None
            if isinstance(e, httpx.HTTPStatusError):
                status_code = e.response.status_code
                error_code = f"HTTP_{status_code}"
                
                # Capture response payload first
                try:
                    response_text = e.response.text
                    # Limit response text size to prevent database issues (max 50KB)
                    max_response_size = 50000
                    if response_text and len(response_text) > max_response_size:
                        response_text = response_text[:max_response_size] + f"\n... (truncated, original size: {len(e.response.text)} bytes)"
                    
                    # Try to parse as JSON for better formatting
                    try:
                        response_json = e.response.json()
                        response_payload = json.dumps(response_json, indent=2)
                        # Limit JSON payload size
                        if len(response_payload) > max_response_size:
                            response_payload = response_payload[:max_response_size] + f"\n... (truncated)"
                        
                        # Extract meaningful error message from Respond.io response
                        if isinstance(response_json, dict):
                            api_message = response_json.get('message') or response_json.get('error') or response_json.get('detail')
                            if api_message:
                                error_msg = f"HTTP {status_code}: {api_message}"
                            else:
                                error_msg = f"HTTP {status_code}: {json.dumps(response_json)}"
                        else:
                            error_msg = f"HTTP {status_code}: {response_json}"
                    except (ValueError, json.JSONDecodeError):
                        # Not JSON, use raw text
                        response_payload = response_text
                        error_msg = f"HTTP {status_code}: {response_text[:500] if response_text else str(e)}"
                except Exception as ex:
                    logger.warning(f"Could not capture response payload: {str(ex)}")
                    error_msg = f"HTTP {status_code}: {str(e)}"
                
                # Log the full response for debugging
                if response_payload:
                    logger.debug(f"Respond.io API error response body: {response_payload[:1000]}")
            elif isinstance(e, httpx.TimeoutException):
                error_code = "TIMEOUT_ERROR"
                error_msg = "Request to Respond.io API timed out"
            elif isinstance(e, httpx.ConnectError):
                error_code = "CONNECTION_ERROR"
                error_msg = f"Failed to connect to Respond.io API: {str(e)}"
            elif isinstance(e, httpx.RequestError):
                error_code = "REQUEST_ERROR"
                error_msg = f"Request error: {str(e)}"
            
            logger.error(f"Error syncing contact name for {contact_id}: {error_msg}", exc_info=True)
            
            # Log the error with response payload
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="respond_contacts",
                    business_id=contact_id,
                    direction="outbound",
                    endpoint=f"/v2/contact/phone:{contact.phone_number}",
                    http_method="GET",
                    status="failed",
                    status_code=status_code,
                    error_code=error_code,
                    error_message=error_msg,
                    response_payload=response_payload  # Already captured above for HTTPStatusError
                ),
                request_payload_dict=request_payload
            )
            raise

    def bulk_sync_contacts_from_respond(self, contact_ids: list[str]) -> dict:
        """Call sync_contact_name for each id; failures are collected without aborting the batch."""
        clean_ids = [str(i).strip() for i in contact_ids if str(i).strip()]
        if not clean_ids:
            return {"succeeded": 0, "failed": 0, "errors": []}
        if len(clean_ids) > 200:
            raise handle_validation_error("Maximum 200 contacts per bulk sync.")
        succeeded = 0
        errors: list[dict] = []
        for cid in clean_ids:
            try:
                self.sync_contact_name(cid)
                succeeded += 1
            except HTTPException as e:
                detail = e.detail
                if isinstance(detail, dict):
                    msg = str(detail.get("message") or detail.get("detail") or detail)
                else:
                    msg = str(detail)
                errors.append({"id": cid, "message": msg})
            except Exception as e:
                errors.append({"id": cid, "message": str(e)})
        return {"succeeded": succeeded, "failed": len(errors), "errors": errors}
    
    def get_or_create_contact(self, phone_number: str, name: Optional[str] = None) -> RespondContact:
        """Get existing contact or create a new one."""
        contact = self.get_contact_by_phone(phone_number)
        if contact is not None:
            # Update name if provided and different
            current_name = getattr(contact, "name", None) or ""
            if name and current_name != name:
                setattr(contact, "name", name)
                self.db.commit()
                self.db.refresh(contact)
            return contact
        
        # Create new contact
        contact_data = RespondContactCreate(phone_number=phone_number, name=name)
        return self.create_contact(contact_data)
