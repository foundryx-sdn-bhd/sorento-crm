"""Built-in documents for every system email (#1349).

Each entry is the default an `email_templates` row with the same `code` starts from
(seeded by the `eml_0002_seed_layouts` migration) and what `EmailTemplateService.render_code`
falls back to when that row is missing or inactive, so a system mail always has a body.
Producers pass CONTEXT to `render_code(code, ctx)`; they never build HTML.

`variables` feeds the editor's variable catalog for that template (key, label, sample);
`sample` is the preview context. Keep both in step with what the producer passes.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.email_system_templates_base import SystemTemplate


def _h() -> dict[str, Any]:
    return {"type": "brand_header"}


def _f(note: Optional[str] = None) -> dict[str, Any]:
    return {"type": "footer", "note": note} if note else {"type": "footer"}


_SAMPLE_BASE = "https://crm.example.com"

SYSTEM_TEMPLATES: dict[str, SystemTemplate] = {}


def _register(t: SystemTemplate) -> None:
    SYSTEM_TEMPLATES[t.code] = t


# --------------------------------------------------------------------------- #
# Auth / users                                                                 #
# --------------------------------------------------------------------------- #

_register(
    SystemTemplate(
        code="auth_password_reset",
        name="Password reset",
        description="Sent when someone asks to reset their password from the sign-in page.",
        subject="Reset your password",
        preheader="Use this link within 1 hour to choose a new password.",
        blocks=[
            _h(),
            {"type": "heading", "text": "Reset your password"},
            {
                "type": "intro",
                "text": "Hi{% if recipient.name %} {{ recipient.name }}{% endif %},\n\nWe received a request to reset the password for your {{ company.name }} account. The button below is valid for 1 hour.",
            },
            {"type": "button", "label": "Reset password", "url": "{{ reset_link }}"},
            {"type": "link", "label": "Or paste this link into your browser:", "url": "{{ reset_link }}"},
            {
                "type": "custom_text",
                "html": "<p>If you did not ask for this, you can ignore this email. Your password stays the same.</p>",
            },
            _f("This is a system-generated email. Please do not reply."),
        ],
        variables=[
            {"key": "recipient.name", "label": "Recipient Name", "sample": "Aina"},
            {"key": "reset_link", "label": "Reset Link", "sample": f"{_SAMPLE_BASE}/change-password?token=sample"},
            {"key": "company.name", "label": "Company Name", "sample": "Sorento"},
        ],
        sample={
            "recipient": {"name": "Aina", "email": "aina@example.com"},
            "reset_link": f"{_SAMPLE_BASE}/change-password?token=sample",
        },
    )
)

_register(
    SystemTemplate(
        code="user_invitation",
        name="User invitation",
        description="Sent when an admin invites a user or resends the invitation.",
        subject="You're invited to join {{ company.name }}",
        preheader="Set your password to start using {{ company.name }}. The link is valid for 7 days.",
        blocks=[
            _h(),
            {"type": "heading", "text": "You're invited to {{ company.name }}"},
            {
                "type": "intro",
                "text": "Hello{% if recipient.name %} {{ recipient.name }}{% endif %},\n\nAn administrator has created an account for you. Set your password to get started. This link is valid for 7 days.",
            },
            {"type": "button", "label": "Set your password", "url": "{{ invite_link }}"},
            {"type": "link", "label": "Or paste this link into your browser:", "url": "{{ invite_link }}"},
            {
                "type": "custom_text",
                "html": "<p>After setting your password, sign in with your email address and the new password.</p>",
            },
            _f("This is a system-generated email. Please do not reply."),
        ],
        variables=[
            {"key": "recipient.name", "label": "Recipient Name", "sample": "Aina"},
            {"key": "invite_link", "label": "Invitation Link", "sample": f"{_SAMPLE_BASE}/change-password?token=sample"},
        ],
        sample={
            "recipient": {"name": "Aina", "email": "aina@example.com"},
            "invite_link": f"{_SAMPLE_BASE}/change-password?token=sample",
        },
    )
)

_register(
    SystemTemplate(
        code="account_email_changed",
        name="Sign-in email updated",
        description="Sent to the NEW address when an admin changes a user's sign-in email.",
        subject="Your sign-in email was updated",
        preheader="Your password is unchanged.",
        blocks=[
            _h(),
            {"type": "heading", "text": "Your sign-in email was updated"},
            {
                "type": "intro",
                "text": "Hello,\n\nAn administrator updated the email you use to sign in to {{ company.name }}. Your password is unchanged.",
            },
            {
                "type": "facts",
                "rows": [
                    {"label": "Previous", "value": "{{ old_email }}"},
                    {"label": "New", "value": "{{ new_email }}"},
                ],
            },
            {"type": "button", "label": "Sign in", "url": "{{ sign_in_link }}"},
            {
                "type": "custom_text",
                "html": "<p>If you did not expect this change, contact your administrator immediately.</p>",
            },
            _f(),
        ],
        variables=[
            {"key": "old_email", "label": "Previous Email", "sample": "old@example.com"},
            {"key": "new_email", "label": "New Email", "sample": "new@example.com"},
            {"key": "sign_in_link", "label": "Sign-in Link", "sample": _SAMPLE_BASE},
        ],
        sample={"old_email": "old@example.com", "new_email": "new@example.com", "sign_in_link": _SAMPLE_BASE},
    )
)

# --------------------------------------------------------------------------- #
# SLA                                                                          #
# --------------------------------------------------------------------------- #

_register(
    SystemTemplate(
        code="sla_daily_summary",
        name="Daily SLA summary",
        description="Daily digest of a staff member's outstanding assigned conversations.",
        subject="Your daily SLA summary ({{ summary_date }})",
        preheader="{{ outstanding_count }} outstanding conversation{% if outstanding_count != 1 %}s{% endif %} as of {{ summary_date }}.",
        blocks=[
            _h(),
            {"type": "heading", "text": "Your daily SLA summary"},
            {
                "type": "intro",
                "text": "Hi{% if recipient.name %} {{ recipient.name }}{% endif %},\n\nHere is where your conversations stand today, {{ summary_date }}.",
            },
            {
                "type": "facts",
                "hide_empty": False,
                "rows": [
                    {"label": "Outstanding today", "value": "{{ outstanding_count }}"},
                    {"label": "Last 7 days", "value": "{{ responded_7 }} responded, {{ resolved_7 }} resolved"},
                    {"label": "Last 30 days", "value": "{{ responded_30 }} responded, {{ resolved_30 }} resolved"},
                ],
            },
            {
                "type": "custom_text",
                "html": (
                    "<h3>Outstanding conversations</h3>"
                    "<table width=\"100%\"><thead><tr><th>Name</th><th>Phone</th><th>Conversation</th></tr></thead><tbody>"
                    "{% for c in conversations %}<tr><td>{{ c.name }}</td><td>{{ c.phone }}</td>"
                    "<td><a href=\"{{ c.link }}\">Open</a></td></tr>"
                    "{% else %}<tr><td colspan=\"3\">No outstanding assigned conversations.</td></tr>{% endfor %}"
                    "</tbody></table>"
                ),
            },
            {"type": "button", "label": "Open my SLA tracking", "url": "{{ summary_link }}"},
            {
                "type": "custom_text",
                "html": "<p style=\"font-size:13px;color:#6b7280;text-align:center;\">Thanks for your help in making {{ company.name }} a better place to work. <a href=\"{{ unsubscribe_link }}\">Unsubscribe from this daily summary</a></p>",
            },
            _f("You receive this summary because you subscribed to the daily SLA summary."),
        ],
        variables=[
            {"key": "summary_date", "label": "Summary Date", "sample": "28/09/2026"},
            {"key": "outstanding_count", "label": "Outstanding Count", "sample": "3"},
            {"key": "responded_7", "label": "Responded (7 days)", "sample": "12"},
            {"key": "resolved_7", "label": "Resolved (7 days)", "sample": "9"},
            {"key": "responded_30", "label": "Responded (30 days)", "sample": "48"},
            {"key": "resolved_30", "label": "Resolved (30 days)", "sample": "41"},
            {"key": "conversations", "label": "Outstanding Conversations (loop: name, phone, link)", "sample": "[...]"},
            {"key": "summary_link", "label": "SLA Tracking Link", "sample": f"{_SAMPLE_BASE}/sla-management/conversation-sla-tracking"},
            {"key": "unsubscribe_link", "label": "Unsubscribe Link", "sample": f"{_SAMPLE_BASE}/unsubscribe/daily-sla-summary?token=sample"},
        ],
        sample={
            "recipient": {"name": "Aina", "email": "aina@example.com"},
            "summary_date": "28/09/2026",
            "outstanding_count": 3,
            "responded_7": 12,
            "resolved_7": 9,
            "responded_30": 48,
            "resolved_30": 41,
            "conversations": [
                {"name": "Tan Ah Kow", "phone": "+60 12-345 6789", "link": f"{_SAMPLE_BASE}/sla-management/conversation-sla-tracking/1"},
                {"name": "Siti Rahmah", "phone": "+60 13-000 0512", "link": f"{_SAMPLE_BASE}/sla-management/conversation-sla-tracking/2"},
                {"name": "Lim Hardware Sdn Bhd", "phone": "+60 3-7788 9900", "link": f"{_SAMPLE_BASE}/sla-management/conversation-sla-tracking/3"},
            ],
            "summary_link": f"{_SAMPLE_BASE}/sla-management/conversation-sla-tracking",
            "unsubscribe_link": f"{_SAMPLE_BASE}/unsubscribe/daily-sla-summary?token=sample",
        },
    )
)

# --------------------------------------------------------------------------- #
# Generic                                                                      #
# --------------------------------------------------------------------------- #

_register(
    SystemTemplate(
        code="notification_generic",
        name="Notification (generic)",
        description="Any notification that has a title and a body but no template of its own.",
        subject="{{ title }}",
        blocks=[
            _h(),
            {"type": "heading", "text": "{{ title }}"},
            {"type": "intro", "text": "{{ body }}"},
            {"type": "button", "label": "{{ link_label | default('Open in Sorento', true) }}", "url": "{{ link }}"},
            _f(),
        ],
        variables=[
            {"key": "title", "label": "Title", "sample": "Import finished"},
            {"key": "body", "label": "Body", "sample": "Your import of 120 rows finished with no errors."},
            {"key": "link", "label": "Link", "sample": f"{_SAMPLE_BASE}/imports/sample"},
            {"key": "link_label", "label": "Button Label", "sample": "Open import"},
        ],
        sample={
            "title": "Import finished",
            "body": "Your import of 120 rows finished with no errors.",
            "link": f"{_SAMPLE_BASE}/imports/sample",
            "link_label": "Open import",
        },
    )
)


# Per-area registries (kept in their own files so they can grow independently).
from app.services import email_system_templates_operations as _operations  # noqa: E402
from app.services import email_system_templates_procurement as _procurement  # noqa: E402

for _t in (*_procurement.TEMPLATES, *_operations.TEMPLATES):
    _register(_t)


# Mails that carry a one-time credential in their context (reset / invite / intake token,
# approver link). They ALWAYS render the built-in document above: no email_templates row
# may override them, no row is seeded for them, and the API refuses the codes. Otherwise
# anyone with email_templates.templates.edit could rewrite the reset mail to send its
# token to their own server (security review B1, PLAN D11). The theme still applies.
CREDENTIAL_CODES: frozenset[str] = frozenset(
    {
        "auth_password_reset",
        "user_invitation",
        "onboarding_intake_link",
        "purchase_request_approval_link",
    }
)


def get_system_template(code: str) -> Optional[SystemTemplate]:
    return SYSTEM_TEMPLATES.get(code)
