"""CRUD service for the AI assistant prompt registry.

Immutable versions + movable labels (see PLAN §5/§8b). Save = append a new
version (``max(version)+1`` per name). Publish/rollback = repoint a label row.
The runtime resolver + cache lives in ``ai_prompt_registry``; this module owns
the admin-facing read/write surface.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.user import User
from app.services import ai_prompt_registry
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.error_handler import AppException

LABELS = ("production", "staging")


class AIPromptRegistryError(AppException):
    """Save-validation failure carrying the contract's structured body
    (``{error, unknown_tokens, missing_vars}``)."""

    def __init__(self, message: str, *, unknown_tokens: list[str], missing_vars: list[str]):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=message,
            code="PROMPT_VALIDATION",
        )
        self.error = message
        self.unknown_tokens = unknown_tokens
        self.missing_vars = missing_vars


class AIPromptService:
    def __init__(self, db: Session):
        self.db = db

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def is_known(name: str) -> bool:
        return name in PROMPT_KEYS

    def _user_names(self, user_ids: list[str]) -> dict[str, str]:
        ids = [u for u in {i for i in user_ids if i}]
        if not ids:
            return {}
        rows = self.db.query(User.id, User.name).filter(User.id.in_(ids)).all()
        return {str(r.id): r.name for r in rows}

    def _labels_map(self, name: str) -> dict[str, Optional[int]]:
        """``{production: int|None, staging: int|None}`` for a key."""
        rows = (
            self.db.query(AIPromptLabel.label, AIPromptVersion.version)
            .join(AIPromptVersion, AIPromptVersion.id == AIPromptLabel.version_id)
            .filter(AIPromptLabel.name == name)
            .all()
        )
        by_label = {r.label: int(r.version) for r in rows}
        return {lbl: by_label.get(lbl) for lbl in LABELS}

    def _latest_version_row(self, name: str) -> Optional[AIPromptVersion]:
        return (
            self.db.query(AIPromptVersion)
            .filter(AIPromptVersion.name == name)
            .order_by(AIPromptVersion.version.desc())
            .first()
        )

    def _labels_for_version(self, name: str) -> dict[str, list[str]]:
        """Map ``version_id`` -> list of labels pointing at it, for a key."""
        rows = (
            self.db.query(AIPromptLabel.label, AIPromptLabel.version_id)
            .filter(AIPromptLabel.name == name)
            .all()
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(str(r.version_id), []).append(r.label)
        return out

    # ---- reads ------------------------------------------------------------

    def list_keys(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for name in ai_prompt_registry.prompt_key_names():
            spec = PROMPT_KEYS[name]
            labels = self._labels_map(name)
            latest = self._latest_version_row(name)
            # last-edited = whichever is more recent: newest version created, or a
            # label last moved (publish). Resolve the actor name for that event.
            updated_at: Optional[datetime] = None
            actor_id: Optional[str] = None
            if latest is not None:
                updated_at = latest.created_at
                actor_id = latest.created_by
            prod_label = (
                self.db.query(AIPromptLabel)
                .filter(AIPromptLabel.name == name, AIPromptLabel.label == "production")
                .first()
            )
            if prod_label is not None and prod_label.updated_at is not None:
                if updated_at is None or prod_label.updated_at > updated_at:
                    updated_at = prod_label.updated_at
                    actor_id = prod_label.updated_by
            names = self._user_names([actor_id] if actor_id else [])
            summaries.append(
                {
                    "name": name,
                    "role": spec.role,
                    "active": spec.active,
                    "activates_in": spec.activates_in,
                    "variables": list(spec.variables),
                    # Whether the dry-run button means anything for this key: the
                    # dry-run runs one whole assistant turn, so a key no turn reads
                    # would report an answer the edit had no part in.
                    "dry_runnable": spec.dry_runnable,
                    "production_version": labels.get("production"),
                    # Which LLM this agent runs on in production. None = the global
                    # assistant model, which is what every agent used before this.
                    "provider": prod_label.provider if prod_label is not None else None,
                    "model": prod_label.model if prod_label is not None else None,
                    "staging_version": labels.get("staging"),
                    "latest_version": int(latest.version) if latest is not None else None,
                    "updated_at": updated_at,
                    "updated_by_name": names.get(str(actor_id)) if actor_id else None,
                }
            )
        return summaries

    def get_versions(self, name: str) -> dict[str, Any]:
        spec = PROMPT_KEYS[name]
        rows = (
            self.db.query(AIPromptVersion)
            .filter(AIPromptVersion.name == name)
            .order_by(AIPromptVersion.version.desc())
            .all()
        )
        label_map = self._labels_for_version(name)
        names = self._user_names([r.created_by for r in rows if r.created_by])
        versions = [
            {
                "id": str(r.id),
                "version": int(r.version),
                "commit_message": r.commit_message,
                "created_by_name": names.get(str(r.created_by)) if r.created_by else None,
                "created_at": r.created_at,
                "labels": label_map.get(str(r.id), []),
            }
            for r in rows
        ]
        return {
            "name": name,
            "role": spec.role,
            "active": spec.active,
            "activates_in": spec.activates_in,
            "variables": list(spec.variables),
            "registry_variables": list(spec.registry_variables),
            "labels": self._labels_map(name),
            "versions": versions,
            # Slice E: always present, so the FE can seed the editor from it
            # when `versions` is empty (no saved version exists yet).
            "fallback_text": spec.fallback(),
        }

    def get_version(self, name: str, version: int) -> dict[str, Any]:
        row = (
            self.db.query(AIPromptVersion)
            .filter(AIPromptVersion.name == name, AIPromptVersion.version == version)
            .first()
        )
        if row is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                message=f"Version {version} not found for prompt '{name}'.",
                code="NOT_FOUND",
            )
        label_map = self._labels_for_version(name)
        names = self._user_names([row.created_by] if row.created_by else [])
        return {
            "id": str(row.id),
            "name": name,
            "version": int(row.version),
            "template": row.template,
            "variables": list(row.variables or []),
            "commit_message": row.commit_message,
            "created_by_name": names.get(str(row.created_by)) if row.created_by else None,
            "created_at": row.created_at,
            "labels": label_map.get(str(row.id), []),
            "missing_vars": [],
        }

    # ---- writes -----------------------------------------------------------

    def save_version(
        self, name: str, *, template: str, commit_message: str, user_id: Optional[str]
    ) -> dict[str, Any]:
        """Append a new immutable version. Unknown ``{{token}}`` hard-blocks
        (422); missing declared var soft-warns (201 + ``missing_vars``); blank
        commit message hard-blocks (422)."""
        if not (commit_message or "").strip():
            raise AIPromptRegistryError(
                "Commit message is required.", unknown_tokens=[], missing_vars=[]
            )
        unknown, missing = ai_prompt_registry.validate_template(name, template)
        if unknown:
            raise AIPromptRegistryError(
                "Template contains unknown variable token(s) that are not declared "
                "for this prompt and would render literally.",
                unknown_tokens=unknown,
                missing_vars=missing,
            )
        spec = PROMPT_KEYS[name]
        next_version = int(
            self.db.query(func.coalesce(func.max(AIPromptVersion.version), 0))
            .filter(AIPromptVersion.name == name)
            .scalar()
            or 0
        ) + 1
        row = AIPromptVersion(
            name=name,
            version=next_version,
            type="text",
            template=template,
            variables=list(spec.variables),
            commit_message=commit_message.strip(),
            created_by=user_id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        names = self._user_names([user_id] if user_id else [])
        return {
            "id": str(row.id),
            "name": name,
            "version": int(row.version),
            "template": row.template,
            "variables": list(row.variables or []),
            "commit_message": row.commit_message,
            "created_by_name": names.get(str(user_id)) if user_id else None,
            "created_at": row.created_at,
            "labels": [],  # a new version lands unlabelled
            "missing_vars": missing,
        }

    def set_agent_model(
        self, name: str, *, label: str, provider: Optional[str], model: Optional[str]
    ) -> dict[str, Optional[str]]:
        """Point one agent at one LLM. Empty/None means "use the global default".

        Deliberately separate from `set_label`: publishing a prompt version and
        changing the model are different decisions with different blast radii, and
        folding them together would make every publish look like a model change in the
        audit trail.
        """
        if label not in LABELS:
            raise AppException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=f"Unknown label '{label}'. Allowed: {', '.join(LABELS)}.",
                code="INVALID_LABEL",
            )
        row = (
            self.db.query(AIPromptLabel)
            .filter(AIPromptLabel.name == name, AIPromptLabel.label == label)
            .first()
        )
        if row is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                message=f"'{name}' has no published {label} version to configure.",
                code="NOT_FOUND",
            )
        row.provider = (provider or "").strip() or None
        row.model = (model or "").strip() or None
        self.db.commit()
        return {"provider": row.provider, "model": row.model}

    def set_label(
        self, name: str, *, label: str, version_id: str, user_id: Optional[str]
    ) -> dict[str, Optional[int]]:
        """Move a label to a version (publish/rollback). One UPSERT + cache bust."""
        if label not in LABELS:
            raise AppException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=f"Unknown label '{label}'. Allowed: {', '.join(LABELS)}.",
                code="INVALID_LABEL",
            )
        version = (
            self.db.query(AIPromptVersion)
            .filter(AIPromptVersion.id == version_id, AIPromptVersion.name == name)
            .first()
        )
        if version is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                message=f"Version not found for prompt '{name}'.",
                code="NOT_FOUND",
            )
        label_row = (
            self.db.query(AIPromptLabel)
            .filter(AIPromptLabel.name == name, AIPromptLabel.label == label)
            .first()
        )
        if label_row is None:
            label_row = AIPromptLabel(
                name=name, label=label, version_id=version_id, updated_by=user_id
            )
            self.db.add(label_row)
        else:
            label_row.version_id = version_id
            label_row.updated_by = user_id
        self.db.commit()
        # Immediate cache invalidation so the very next chat turn uses the new
        # version (UAC B4) - do not wait for the TTL.
        ai_prompt_registry.bust_cache(name)
        return self._labels_map(name)
