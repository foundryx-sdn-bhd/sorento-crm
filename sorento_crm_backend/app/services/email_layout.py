"""The one branded layout every outgoing email renders through (#1349).

Three layers, the dreamz-ems split without its multi-tenant model:

1. **Brand values** - `EmailTheme`, stored as one JSON object on the `system_settings`
   singleton (`system_settings.email_theme`) and merged over defaults derived from the
   fields that singleton already carries (name, logo, address, support email, socials).
2. **Base layout** - `app/templates/email/base.html` + `layout.html` (Jinja inheritance,
   a SandboxedEnvironment, every style inline, 600px table card, phone breakpoint at 620px;
   a document that says `width: "wide"` gets the 900px card with 24px gutters instead).
3. **Block document** - `EmailDocument`: an ordered list of blocks (brand header, heading,
   intro, facts table, CTA button, secondary link, custom text, footer). Each block's
   admin-authored Jinja renders in the SAME sandbox `templating.py` uses for templates,
   then the layout places the results.

`render_document` never lets a template error reach a recipient: any failure is logged
and the mail is rebuilt from the plain safe document (subject + text) - never a
`[template-error:...]` string in a sent mail.
"""
from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from jinja2 import FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup
from pydantic import BaseModel, Field, field_validator

from app.services.templating import html_to_text, render_budget, render_html_strict, render_text_strict

logger = logging.getLogger(__name__)

LAYOUT_MARKER = "data-sorento-layout"

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

# The layout itself is ours, not admin-authored, but it still renders in a sandbox with
# autoescape on: theme values (company name, address, footer note) are admin-typed.
_layout_env = SandboxedEnvironment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
    trim_blocks=False,
    lstrip_blocks=False,
)

# --------------------------------------------------------------------------- #
# Theme                                                                        #
# --------------------------------------------------------------------------- #

# System font stacks only. Gmail, Outlook and Yahoo do not load web fonts, so a web font
# would be a promise the inbox breaks; each key names a stack every client has.
FONT_STACKS: dict[str, str] = {
    "system": "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif",
    "arial": "Arial,Helvetica,sans-serif",
    "helvetica": "'Helvetica Neue',Helvetica,Arial,sans-serif",
    "verdana": "Verdana,Geneva,sans-serif",
    "trebuchet": "'Trebuchet MS',Helvetica,Arial,sans-serif",
    "georgia": "Georgia,'Times New Roman',serif",
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL_RE = re.compile(r"^[^@\s?&#/:]+@[^@\s?&#/:]+\.[^@\s?&#/:]+$")

DEFAULT_PRIMARY = "#2563EB"
DEFAULT_FOOTER_NOTE = (
    "You received this email because you have an account on {company}. "
    "It is a service message about your work in the system."
)


def _hex(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    v = str(value).strip()
    if len(v) == 4 and v.startswith("#"):
        v = "#" + "".join(ch * 2 for ch in v[1:])
    if not _HEX_RE.match(v):
        raise ValueError("must be a hex colour like #2563EB")
    return v.upper()


def _safe_url(value: Optional[str]) -> Optional[str]:
    """http(s), mailto and site-relative only; anything else (javascript:, data:) is dropped."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    lowered = v.lower()
    if lowered.startswith(("//", "/\\")):
        return None  # protocol-relative: another host, not this site
    if lowered.startswith(("http://", "https://", "mailto:", "/")):
        return v
    return None


class SocialLink(BaseModel):
    label: str = Field(..., min_length=1, max_length=40)
    url: str = Field(..., min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if _safe_url(v) is None:
            raise ValueError("must start with http://, https:// or mailto:")
        return v.strip()


class EmailTheme(BaseModel):
    """What an admin stores. Every field is optional: blank means "use the default",
    and the default is derived from System Settings so an untouched install is branded."""

    logo_url: Optional[str] = Field(None, max_length=500)
    logo_alignment: Optional[Literal["left", "center"]] = None
    logo_height: Optional[int] = Field(None, ge=20, le=80)
    header_style: Optional[Literal["brand", "white"]] = None
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    page_background: Optional[str] = None
    button_color: Optional[str] = None
    button_text_color: Optional[str] = None
    button_radius: Optional[int] = Field(None, ge=0, le=32)
    button_width: Optional[Literal["auto", "full"]] = None
    card_radius: Optional[int] = Field(None, ge=0, le=24)
    font: Optional[Literal["system", "arial", "helvetica", "verdana", "trebuchet", "georgia"]] = None
    company_name: Optional[str] = Field(None, max_length=120)
    address: Optional[str] = Field(None, max_length=300)
    help_email: Optional[str] = Field(None, max_length=200)
    help_url: Optional[str] = Field(None, max_length=500)
    footer_note: Optional[str] = Field(None, max_length=400)
    social_links: Optional[list[SocialLink]] = Field(None, max_length=8)

    @field_validator(
        "primary_color", "accent_color", "page_background", "button_color", "button_text_color"
    )
    @classmethod
    def _colour(cls, v: Optional[str]) -> Optional[str]:
        return _hex(v)

    @field_validator("logo_url", "help_url")
    @classmethod
    def _url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        if _safe_url(v) is None or str(v).strip().lower().startswith("mailto:"):
            raise ValueError("must start with http:// or https://")
        return str(v).strip()

    @field_validator("company_name", "address", "help_email", "footer_note")
    @classmethod
    def _blank_is_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    @field_validator("help_email")
    @classmethod
    def _email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError("must be an email address")
        return v


class ResolvedTheme(BaseModel):
    """Every field filled - what the layout renders with."""

    logo_url: Optional[str]
    logo_alignment: Literal["left", "center"]
    logo_height: int
    header_style: Literal["brand", "white"]
    primary_color: str
    accent_color: str
    page_background: str
    button_color: str
    button_text_color: str
    button_radius: int
    button_width: Literal["auto", "full"]
    card_radius: int
    font: str
    company_name: str
    address: Optional[str]
    help_email: Optional[str]
    help_url: Optional[str]
    footer_note: Optional[str]
    social_links: list[SocialLink]

    def layout_vars(self) -> dict[str, Any]:
        data = self.model_dump()
        # Markup: the stacks are our own constants (no double quote in any of them), and
        # autoescaping their single quotes to &#39; would be valid HTML but noisy in source.
        data["font_stack"] = Markup(FONT_STACKS.get(self.font, FONT_STACKS["system"]))
        data["header_background"] = self.primary_color if self.header_style == "brand" else "#FFFFFF"
        data["header_text_color"] = "#FFFFFF" if self.header_style == "brand" else "#111827"
        data["social_links"] = [s.model_dump() for s in self.social_links]
        return data


_SOCIAL_FIELDS = (
    ("social_facebook", "Facebook"),
    ("social_instagram", "Instagram"),
    ("social_linkedin", "LinkedIn"),
    ("social_youtube", "YouTube"),
    ("social_twitter", "X"),
    ("social_pinterest", "Pinterest"),
)


def theme_defaults(settings: Any = None, company_logo_url: Optional[str] = None) -> dict[str, Any]:
    """Defaults for every theme field, from the System Settings row (may be None)."""
    company = (getattr(settings, "name", None) or "").strip() or "Sorento"
    logo = (getattr(settings, "logo", None) or "").strip() or (company_logo_url or "").strip()
    socials = []
    for field, label in _SOCIAL_FIELDS:
        url = (getattr(settings, field, None) or "").strip()
        # SocialLink caps url at 500; an over-long settings value is skipped, never raised
        # (load_theme must not fail a mail).
        if url and len(url) <= 500 and _safe_url(url):
            socials.append({"label": label, "url": url})
    help_url = (getattr(settings, "website_url", None) or "").strip() or None
    return {
        "logo_url": logo if _safe_url(logo) and not logo.lower().startswith("mailto:") else None,
        "logo_alignment": "center",
        "logo_height": 36,
        "header_style": "brand",
        "primary_color": DEFAULT_PRIMARY,
        "accent_color": DEFAULT_PRIMARY,
        "page_background": "#F3F4F6",
        "button_color": None,  # follows primary_color
        "button_text_color": "#FFFFFF",
        "button_radius": 8,
        "button_width": "auto",
        "card_radius": 12,
        "font": "system",
        "company_name": company,
        "address": (getattr(settings, "address", None) or "").strip() or None,
        "help_email": (getattr(settings, "support_email", None) or "").strip() or None,
        "help_url": help_url if help_url and _safe_url(help_url) else None,
        "footer_note": DEFAULT_FOOTER_NOTE.format(company=company),
        "social_links": socials,
    }


def resolve_theme(stored: Optional[dict[str, Any]], defaults: dict[str, Any]) -> ResolvedTheme:
    """Stored overrides over defaults. A stored value that no longer validates is ignored
    field by field (logged), so one bad value can never stop mail from rendering."""
    merged = dict(defaults)
    for key, value in (stored or {}).items():
        if key not in EmailTheme.model_fields or value is None or value == "":
            continue
        try:
            checked = EmailTheme.model_validate({key: value})
        except Exception as exc:  # noqa: BLE001
            logger.warning("email theme field %s ignored: %s", key, exc)
            continue
        merged[key] = getattr(checked, key)
    if not merged.get("button_color"):
        merged["button_color"] = merged["primary_color"]
    socials = merged.get("social_links") or []
    merged["social_links"] = [s if isinstance(s, SocialLink) else SocialLink(**s) for s in socials]
    return ResolvedTheme(**merged)


def first_company_logo(db: Any) -> Optional[str]:
    """The first active company's logo URL (the theme's logo fallback), or None."""
    try:
        from app.models.company import Company

        row = (
            db.query(Company.logo_url)
            .filter(Company.is_active.is_(True), Company.logo_url.isnot(None))
            .order_by(Company.name.asc())
            .first()
        )
        return row[0] if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("email theme: company logo unreadable: %s", exc)
        return None


def load_theme(db: Any) -> ResolvedTheme:
    """The effective theme for this install. Never raises: a DB problem yields defaults."""
    settings = None
    try:
        from app.models.user import SystemSetting

        settings = db.query(SystemSetting).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("email theme: system settings unreadable: %s", exc)
    stored = getattr(settings, "email_theme", None) if settings is not None else None
    return resolve_theme(
        stored if isinstance(stored, dict) else None,
        theme_defaults(settings, first_company_logo(db)),
    )


# --------------------------------------------------------------------------- #
# Block document                                                               #
# --------------------------------------------------------------------------- #

Align = Literal["left", "center"]


class _Block(BaseModel):
    id: Optional[str] = Field(None, max_length=64)


class BrandHeaderBlock(_Block):
    type: Literal["brand_header"] = "brand_header"


class HeadingBlock(_Block):
    type: Literal["heading"] = "heading"
    text: str = Field("", max_length=500)
    align: Align = "left"


class IntroBlock(_Block):
    type: Literal["intro"] = "intro"
    text: str = Field("", max_length=5000)
    align: Align = "left"


class FactRow(BaseModel):
    label: str = Field("", max_length=200)
    value: str = Field("", max_length=2000)


class FactsBlock(_Block):
    type: Literal["facts"] = "facts"
    rows: list[FactRow] = Field(default_factory=list, max_length=40)
    hide_empty: bool = True


class ButtonBlock(_Block):
    type: Literal["button"] = "button"
    label: str = Field("Open", max_length=120)
    url: str = Field("", max_length=2000)


class LinkBlock(_Block):
    type: Literal["link"] = "link"
    label: str = Field("Or paste this link into your browser:", max_length=200)
    url: str = Field("", max_length=2000)


class CustomTextBlock(_Block):
    type: Literal["custom_text"] = "custom_text"
    html: str = Field("", max_length=200_000)


class FooterBlock(_Block):
    type: Literal["footer"] = "footer"
    note: Optional[str] = Field(None, max_length=400)


EmailBlock = Annotated[
    Union[
        BrandHeaderBlock,
        HeadingBlock,
        IntroBlock,
        FactsBlock,
        ButtonBlock,
        LinkBlock,
        CustomTextBlock,
        FooterBlock,
    ],
    Field(discriminator="type"),
]

BLOCK_TYPES = (
    "brand_header",
    "heading",
    "intro",
    "facts",
    "button",
    "link",
    "custom_text",
    "footer",
)


#: The card widths the shell renders (EMAIL-HANDOVER-QTY, owner 30 Sep: the OI
#: handover's nine-column line table was "cramped" in the 600 card). `wide` is for a
#: table email: 900px card, 24px side gutters, phone breakpoint at 920. Every other mail,
#: and every stored document that says nothing, is `standard` - exactly the shell #1349
#: shipped. `print` (OI-EMAIL-PRINT, owner 9 Oct: "fit in one page to print out") is a
#: table email that purchasing prints: 672px card, 12px gutters, so the card plus the
#: shell's 12px outer padding stays inside A4 portrait's printable width (698px at
#: Outlook's 0.5in margins) with no scaling and nothing clipped.
DocumentWidth = Literal["standard", "wide", "print"]

_CARD_WIDTH: dict[str, int] = {"standard": 600, "wide": 900, "print": 672}
_SIDE_GUTTER: dict[str, int] = {"standard": 40, "wide": 24, "print": 12}


class EmailDocument(BaseModel):
    version: Literal[1] = 1
    blocks: list[EmailBlock] = Field(default_factory=list, max_length=40)
    width: DocumentWidth = "standard"


def implicit_document(body_html: str) -> EmailDocument:
    """The document a template with no `layout_json` renders as: its body between the
    brand header and the footer. Admin-authored templates join the layout this way with
    no data migration."""
    return EmailDocument(
        blocks=[
            BrandHeaderBlock(),
            CustomTextBlock(html=body_html or ""),
            FooterBlock(),
        ]
    )


def parse_document(layout_json: Any, body_html: str = "") -> EmailDocument:
    if not layout_json:
        return implicit_document(body_html)
    if isinstance(layout_json, EmailDocument):
        return layout_json
    return EmailDocument.model_validate(layout_json)


# --------------------------------------------------------------------------- #
# Custom HTML defaults (the "inlined CSS" for admin-authored rich text)        #
# --------------------------------------------------------------------------- #

_TAG_DEFAULTS: dict[str, str] = {
    "p": "margin:0 0 16px 0;",
    "h1": "margin:0 0 12px 0;font-size:22px;line-height:30px;color:#111827;",
    "h2": "margin:0 0 12px 0;font-size:18px;line-height:26px;color:#111827;",
    "h3": "margin:0 0 8px 0;font-size:16px;line-height:24px;color:#111827;",
    "ul": "margin:0 0 16px 0;padding-left:20px;",
    "ol": "margin:0 0 16px 0;padding-left:20px;",
    "li": "margin:0 0 4px 0;",
    "blockquote": "margin:0 0 16px 0;padding:8px 16px;border-left:3px solid #e5e7eb;color:#4b5563;",
    "table": "border-collapse:collapse;margin:0 0 16px 0;font-size:13px;line-height:18px;",
    "th": "padding:8px 10px;border:1px solid #e5e7eb;background-color:#f9fafb;text-align:left;font-weight:600;color:#374151;",
    "td": "padding:8px 10px;border:1px solid #e5e7eb;color:#111827;",
    "hr": "border:0;border-top:1px solid #e5e7eb;margin:20px 0;",
}

_START_TAG_RE = re.compile(
    r"<(p|h1|h2|h3|ul|ol|li|blockquote|table|th|td|hr|a)(\s[^<>]*?)?(/?)>", re.IGNORECASE
)
_STYLE_ATTR_RE = re.compile(r"""\sstyle\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)


def inline_defaults(html: str, accent_color: str) -> str:
    """Give admin-authored tags inline default styles. An authored `style` wins: the
    defaults are written FIRST inside the attribute, so a later declaration overrides."""
    if not html:
        return html

    def _sub(m: re.Match) -> str:
        tag = m.group(1).lower()
        attrs = m.group(2) or ""
        closing = m.group(3) or ""
        default = (
            f"color:{accent_color};text-decoration:underline;" if tag == "a" else _TAG_DEFAULTS[tag]
        )
        sm = _STYLE_ATTR_RE.search(attrs)
        if sm:
            quote, existing = sm.group(1), sm.group(2)
            new_attr = f" style={quote}{default}{existing}{quote}"
            attrs = attrs[: sm.start()] + new_attr + attrs[sm.end():]
        else:
            attrs = f'{attrs} style="{default}"'
        return f"<{m.group(1)}{attrs}{closing}>"

    return _START_TAG_RE.sub(_sub, html)


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class RenderedEmail:
    subject: str
    body_html: str
    body_text: str
    fallback_used: bool = False
    error: Optional[str] = None

    def as_dict(self) -> dict[str, str]:
        return {"subject": self.subject, "body_html": self.body_html, "body_text": self.body_text}


# A long run of zero-width non-joiners after the preheader stops clients pulling the
# first body line into the inbox preview after it.
_PREHEADER_PADDING = Markup("&#847;&zwnj;&nbsp;" * 60)

_JINJA_ANY_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)


def _one_line(value: str) -> str:
    return " ".join((value or "").split())


def _render_block(block: Any, context: dict[str, Any], theme: ResolvedTheme) -> Optional[dict[str, Any]]:
    """Render one block's Jinja fields. Returns a dict for the layout, or None to drop it.
    Raises on a template error (strict)."""
    t = block.type
    if t in ("brand_header", "footer"):
        out: dict[str, Any] = {"type": t}
        if t == "footer":
            note = render_text_strict(block.note, context).strip() if block.note else ""
            out["note"] = note or None
        return out
    if t == "heading":
        text = _one_line(render_text_strict(block.text, context))
        return {"type": t, "text": text, "align": block.align} if text else None
    if t == "intro":
        text = render_text_strict(block.text, context).strip()
        if not text:
            return None
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        html = "".join(
            f'<p style="margin:0 0 12px 0;">{_html.escape(p).replace(chr(10), "<br>")}</p>' for p in paras
        )
        return {"type": t, "html": Markup(html), "text": text, "align": block.align}
    if t == "facts":
        rows = []
        for row in block.rows:
            label = _one_line(render_text_strict(row.label, context))
            value = render_text_strict(row.value, context).strip()
            if block.hide_empty and (not value or value in ("-", "None")):
                continue
            rows.append({"label": label, "value": value})
        return {"type": t, "rows": rows} if rows else None
    if t in ("button", "link"):
        url = _safe_url(render_text_strict(block.url, context).strip())
        label = _one_line(render_text_strict(block.label, context))
        if not url:
            return None
        return {"type": t, "url": url, "label": label}
    if t == "custom_text":
        html = render_html_strict(block.html, context)
        if not html.strip() or not re.sub(r"<[^>]+>|&nbsp;|\s", "", html):
            return None
        return {"type": t, "html": Markup(inline_defaults(html, theme.accent_color)), "raw": html}
    return None


def _block_text(block: dict[str, Any], theme: ResolvedTheme) -> str:
    t = block["type"]
    if t == "heading":
        return block["text"]
    if t == "intro":
        return block["text"]
    if t == "facts":
        return "\n".join(f"{r['label']}: {r['value']}" for r in block["rows"])
    if t == "button":
        return f"{block['label']}: {block['url']}"
    if t == "link":
        return f"{block['label']} {block['url']}"
    if t == "custom_text":
        return html_to_text(block["raw"])
    if t == "footer":
        lines = [theme.company_name or ""]
        if theme.address:
            lines.append(theme.address)
        if theme.help_email:
            lines.append(f"Need help? {theme.help_email}")
        note = block.get("note") or theme.footer_note
        if note:
            lines.append(note)
        return "-- \n" + "\n".join(line for line in lines if line)
    return ""


def _layout(
    *,
    subject: str,
    preheader: str,
    blocks: list[dict[str, Any]],
    theme: ResolvedTheme,
    width: str = "standard",
) -> str:
    first_content = next(
        (i for i, b in enumerate(blocks) if b["type"] != "brand_header"), 0
    )
    card_width = _CARD_WIDTH.get(width, _CARD_WIDTH["standard"])
    template = _layout_env.get_template("layout.html")
    return template.render(
        subject=subject,
        preheader=preheader,
        preheader_padding=_PREHEADER_PADDING,
        blocks=blocks,
        theme=theme.layout_vars(),
        first_content_index=first_content,
        has_footer=any(b["type"] == "footer" for b in blocks),
        card_width=card_width,
        # The phone breakpoint sits 20px past the card, as 620 did past 600.
        breakpoint=card_width + 20,
        gutter=_SIDE_GUTTER.get(width, _SIDE_GUTTER["standard"]),
    )


def _derive_preheader(blocks: list[dict[str, Any]]) -> str:
    for b in blocks:
        if b["type"] == "intro":
            return _one_line(b["text"])[:140]
    return ""


def render_document(
    doc: EmailDocument,
    *,
    subject: str,
    context: dict[str, Any],
    theme: ResolvedTheme,
    preheader: Optional[str] = None,
    body_text: Optional[str] = None,
) -> RenderedEmail:
    """Render subject + blocks + layout. On ANY failure: log it and return the plain safe
    layout built from whatever still renders (subject, text) - never an error marker."""
    try:
        with render_budget(3.0):
            return _render_document_inner(doc, subject=subject, context=context, theme=theme, preheader=preheader, body_text=body_text)
    except Exception as exc:  # noqa: BLE001 - a recipient never sees this
        logger.error("email render failed, sending the safe layout: %s", exc, exc_info=True)
        return _fallback(doc, subject=subject, context=context, theme=theme, body_text=body_text, error=str(exc))


def _render_document_inner(
    doc: EmailDocument,
    *,
    subject: str,
    context: dict[str, Any],
    theme: ResolvedTheme,
    preheader: Optional[str],
    body_text: Optional[str],
) -> RenderedEmail:
    rendered_subject = _one_line(render_text_strict(subject or "", context))
    blocks = []
    for block in doc.blocks:
        r = _render_block(block, context, theme)
        if r is not None:
            blocks.append(r)
    pre = _one_line(render_text_strict(preheader, context)) if preheader else _derive_preheader(blocks)
    if body_text and body_text.strip():
        text = render_text_strict(body_text, context).strip()
    else:
        button_urls = {b["url"] for b in blocks if b["type"] == "button"}
        text = "\n\n".join(
            s
            for s in (
                _block_text(b, theme)
                for b in blocks
                # The secondary link repeats the button's URL for HTML readers whose
                # client hides the button; in plain text it would print twice.
                if not (b["type"] == "link" and b["url"] in button_urls)
            )
            if s
        ).strip()
    html = _layout(
        subject=rendered_subject, preheader=pre, blocks=blocks, theme=theme, width=doc.width
    )
    return RenderedEmail(subject=rendered_subject, body_html=html, body_text=text)


def _lenient(fn, *args) -> Optional[Any]:
    """Best effort under its own small budget: in the fallback one runaway block must not
    starve the others of the time they need to put their text in the mail."""
    try:
        with render_budget(0.1):
            return fn(*args)
    except Exception:  # noqa: BLE001
        return None


def _fallback(
    doc: EmailDocument,
    *,
    subject: str,
    context: dict[str, Any],
    theme: ResolvedTheme,
    body_text: Optional[str],
    error: str,
) -> RenderedEmail:
    subj = _lenient(render_text_strict, subject or "", context)
    if subj is None:
        subj = _JINJA_ANY_RE.sub("", subject or "")
    subj = _one_line(subj) or "Notification"

    text = None
    if body_text and body_text.strip():
        text = _lenient(render_text_strict, body_text, context)
    if not text:
        parts = []
        for block in doc.blocks:
            r = _lenient(_render_block, block, context, theme)
            if r is not None and r["type"] not in ("brand_header", "footer"):
                s = _lenient(_block_text, r, theme)
                if s:
                    parts.append(s)
        text = "\n\n".join(parts)
    text = (text or "").strip() or subj

    return plain_layout(subject=subj, text=text, theme=theme, error=error)


_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.IGNORECASE | re.DOTALL)


def _body_inner(html: str) -> str:
    """A producer that sent a whole document (<!DOCTYPE><html><body>...) gets only its body
    placed in the card; a document nested in a table cell is not valid HTML."""
    m = _BODY_RE.search(html)
    return m.group(1).strip() if m else html


def plain_layout(
    *,
    subject: str,
    text: str,
    theme: ResolvedTheme,
    html: Optional[str] = None,
    error: Optional[str] = None,
) -> RenderedEmail:
    """The safe layout: brand header, the text (or already-rendered HTML) as the body,
    footer. Used by the fallback and by the outbox safety net for mail that arrives with
    no layout. Nothing here evaluates admin Jinja, so nothing here can fail on it."""
    if html and html.strip():
        body = inline_defaults(_body_inner(html), theme.accent_color)
    else:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
        body = "".join(
            f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras
        )
        body = inline_defaults(body, theme.accent_color)
    blocks: list[dict[str, Any]] = [{"type": "brand_header"}]
    if body:
        blocks.append({"type": "custom_text", "html": Markup(body), "raw": body})
    blocks.append({"type": "footer", "note": None})
    try:
        out = _layout(subject=subject, preheader="", blocks=blocks, theme=theme)
    except Exception as exc:  # noqa: BLE001 - last resort: no theme at all
        logger.error("email safe layout failed, sending bare HTML: %s", exc, exc_info=True)
        out = (
            f'<!DOCTYPE html><html><body {LAYOUT_MARKER}="1" style="font-family:Arial,sans-serif;">'
            f"<h1 style=\"font-size:20px;\">{_html.escape(subject)}</h1>{body}</body></html>"
        )
    return RenderedEmail(
        subject=subject,
        body_html=out,
        body_text=(text or "").strip(),
        fallback_used=error is not None,
        error=error,
    )


def has_layout(body_html: Optional[str]) -> bool:
    return bool(body_html) and LAYOUT_MARKER in (body_html or "")
