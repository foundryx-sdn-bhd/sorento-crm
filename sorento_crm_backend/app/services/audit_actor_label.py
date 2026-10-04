"""The audit actor in words (identity S0, AC-13). Never an id.

One copy, shared by the audit log list (`GET /api/v1/audit/logs/`) and the activity
timeline (`activity_service`), so both say "<admin> on behalf of <target>" the same way.
"""
from __future__ import annotations

from typing import Optional


def on_behalf_of_label(real_name: str, effective_name: str) -> str:
    """An impersonated write: who was at the keyboard, for whom (plan 8.1)."""
    return f"{real_name} on behalf of {effective_name}"


# Sign-in method words inside an actor label (contract section 5).
METHOD_WORDS = {
    "password": "email",
    "phone_otp": "phone",
    "portal_link": "portal",
    "portal_token": "portal",
    "api_key": "API key",
    "impersonation": "impersonation",
    "dev_login": "dev sign-in",
}


def actor_label(it, user_names: dict[str, str], contact_names: dict[str, str],
                 integration_names: dict[str, str], legacy_label: str,
                 scheduled_names: Optional[dict[str, str]] = None) -> str:
    """Who did it, in words (identity S0, AC-13). Never an id.

    ``scheduled_names`` maps a scheduled task's key (a scheduler row's ``job_id``)
    to its name; a tick with no scheduled task row keeps its key.
    """
    actor_type = getattr(it, "actor_type", None)
    user_id = str(it.user_id) if it.user_id is not None else None
    real_user_id = str(it.real_user_id) if getattr(it, "real_user_id", None) is not None else None
    contact_id = str(it.contact_id) if getattr(it, "contact_id", None) is not None else None

    def _user(uid: Optional[str]) -> Optional[str]:
        return (user_names.get(uid) or "Unknown user") if uid else None

    if actor_type == "user":
        impersonating = it.auth_method == "impersonation" or (
            real_user_id is not None and real_user_id != user_id
        )
        if impersonating and real_user_id:
            on_behalf = _user(user_id) or (contact_names.get(contact_id) if contact_id else None) or "Unknown user"
            return on_behalf_of_label(_user(real_user_id), on_behalf)
        name = _user(user_id or real_user_id) or (contact_names.get(contact_id) if contact_id else None) or "Unknown user"
        word = METHOD_WORDS.get(it.auth_method or "")
        return f"{name} ({word})" if word else name
    if actor_type == "integration":
        integration = integration_names.get(str(it.integration_id)) if getattr(it, "integration_id", None) else None
        label = f"Integration: {integration or 'unknown'}"
        return f"{label} as {_user(user_id)}" if user_id else label
    if actor_type == "worker":
        return f"Background job for {_user(user_id)}" if user_id else "Background job"
    if actor_type == "scheduler":
        job_id = getattr(it, "job_id", None)
        if not job_id:
            return "Scheduled"
        return f"Scheduled: {(scheduled_names or {}).get(job_id) or job_id}"
    if actor_type == "contact":
        return f"Portal: {contact_names.get(contact_id) or 'unknown contact'} (no user)"
    if actor_type == "public_link":
        return "Public link"
    if actor_type == "system":
        return "System"
    return legacy_label
