"""The shared email layout (#1349): parts, text part, inline CSS, theme, fallback.

Pure rendering tests - no database. AC ids refer to
documentation/plans/email/email-layout-28sep-acceptance-criteria.md.
"""
from __future__ import annotations

import logging
import re

import pytest

from app.services.email_layout import (
    LAYOUT_MARKER,
    EmailDocument,
    EmailTheme,
    has_layout,
    implicit_document,
    inline_defaults,
    parse_document,
    render_document,
    resolve_theme,
    theme_defaults,
)


class _Settings:
    name = "Acme Trading"
    logo = "https://cdn.example.com/logo.png"
    address = "1 Jalan Satu, 50000 Kuala Lumpur"
    support_email = "help@acme.example"
    website_url = "https://help.acme.example"
    social_facebook = "https://facebook.com/acme"
    social_instagram = None
    social_linkedin = "https://linkedin.com/company/acme"
    social_youtube = ""
    social_twitter = "javascript:alert(1)"
    social_pinterest = None


def _theme(**overrides):
    return resolve_theme(overrides, theme_defaults(_Settings()))


def _doc(*blocks):
    return EmailDocument.model_validate({"version": 1, "blocks": list(blocks)})


FULL = _doc(
    {"type": "brand_header"},
    {"type": "heading", "text": "Hello {{ who }}"},
    {"type": "intro", "text": "First para.\n\nSecond {{ who }} para."},
    {"type": "facts", "rows": [
        {"label": "Customer", "value": "{{ customer }}"},
        {"label": "Empty", "value": "{{ missing_ok | default('', true) }}"},
        {"label": "Dash", "value": "-"},
    ]},
    {"type": "button", "label": "Open it", "url": "{{ link }}"},
    {"type": "link", "label": "Or paste:", "url": "{{ link }}"},
    {"type": "custom_text", "html": "<p>Custom <a href=\"{{ link }}\">x</a></p>"},
    {"type": "footer"},
)
CTX = {"who": "Aina", "customer": "Lim & Sons", "link": "https://crm.example.com/x/1"}


def _render(doc=FULL, ctx=CTX, theme=None, **kw):
    return render_document(doc, subject=kw.pop("subject", "Subj {{ who }}"), context=ctx, theme=theme or _theme(), **kw)


# --- AC-EM001 container ---------------------------------------------------------
def test_container_is_600_table_card_on_page_background():
    r = _render()
    assert r.body_html.lstrip().startswith("<!DOCTYPE html>")
    assert 'width="600"' in r.body_html and "max-width:600px" in r.body_html
    assert "background-color:#F3F4F6" in r.body_html
    assert "border-radius:12px" in r.body_html
    assert f'{LAYOUT_MARKER}="1"' in r.body_html
    assert has_layout(r.body_html)


# --- AC-EM002 brand header ------------------------------------------------------
def test_brand_header_shows_logo_with_company_alt_on_brand_band():
    r = _render(theme=_theme(primary_color="#FF5A00"))
    assert '<img src="https://cdn.example.com/logo.png" alt="Acme Trading"' in r.body_html
    assert "background-color:#FF5A00" in r.body_html


def test_brand_header_without_logo_shows_company_name_and_white_style():
    theme = resolve_theme({"header_style": "white"}, theme_defaults(None))
    r = _render(theme=theme)
    assert "<img" not in r.body_html
    assert ">Sorento</span>" in r.body_html
    assert "border-bottom:1px solid #eef0f3" in r.body_html


# --- AC-EM003 preheader ---------------------------------------------------------
def test_preheader_rendered_hidden_first_in_body():
    r = _render(preheader="Heads up {{ who }}")
    body = r.body_html.split("<body", 1)[1]
    first_div = body.split("<div", 1)[1]
    assert first_div.lstrip().startswith('style="display:none;')
    assert "Heads up Aina" in first_div.split("</div>", 1)[0]
    assert "&zwnj;" in r.body_html


def test_preheader_defaults_to_intro_text():
    r = _render()
    assert 'aria-hidden="true">First para. Second Aina para.' in r.body_html


def test_no_preheader_without_intro():
    r = _render(_doc({"type": "brand_header"}, {"type": "heading", "text": "Only"}))
    assert 'aria-hidden="true"' not in r.body_html


# --- AC-EM004 / 005 CTA and link --------------------------------------------------
def test_button_is_bulletproof_centred_with_theme_style():
    r = _render(theme=_theme(button_color="#111111", button_text_color="#FFEEDD", button_radius=20))
    assert 'bgcolor="#111111"' in r.body_html
    assert 'href="https://crm.example.com/x/1"' in r.body_html
    m = re.search(r'<a class="em-btn-link"[^>]*style="([^"]+)"', r.body_html)
    assert m and "color:#FFEEDD" in m.group(1) and "border-radius:20px" in m.group(1)
    assert "padding:16px 40px" in m.group(1) and "font-size:16px" in m.group(1)
    assert ">Open it</a>" in r.body_html


def test_full_width_button():
    r = _render(theme=_theme(button_width="full"))
    assert re.search(r'class="em-btn-table"[^>]*width="100%"', r.body_html)


def test_button_with_empty_url_is_not_rendered():
    r = _render(_doc({"type": "button", "label": "Go", "url": "{{ nothing | default('', true) }}"}))
    assert '<a class="em-btn-link"' not in r.body_html


def test_secondary_link_shows_raw_url():
    r = _render()
    assert "Or paste:<br>" in r.body_html
    assert ">https://crm.example.com/x/1</a>" in r.body_html


# --- AC-EM006 facts ------------------------------------------------------------------
def test_facts_rows_escape_and_hide_empty():
    r = _render()
    assert "Lim &amp; Sons" in r.body_html
    assert ">Empty<" not in r.body_html
    assert ">Dash<" not in r.body_html


def test_facts_keep_empty_when_hide_empty_off():
    doc = _doc({"type": "facts", "hide_empty": False, "rows": [{"label": "Zero", "value": "-"}]})
    assert ">Zero<" in _render(doc).body_html


# --- AC-EM007 footer -------------------------------------------------------------------
def test_footer_carries_company_address_help_socials_and_note():
    r = _render()
    html = r.body_html
    assert "Acme Trading" in html
    assert "1 Jalan Satu, 50000 Kuala Lumpur" in html
    assert 'href="mailto:help@acme.example"' in html
    assert 'href="https://help.acme.example"' in html
    assert ">Facebook</a>" in html and ">LinkedIn</a>" in html
    assert "javascript:" not in html  # unsafe social dropped at default time
    assert "You received this email because you have an account on Acme Trading" in html


def test_footer_note_override_per_mail():
    doc = _doc({"type": "footer", "note": "You get this because {{ why }}."})
    r = _render(doc, ctx={"why": "you asked"})
    assert "You get this because you asked." in r.body_html
    assert "have an account on" not in r.body_html


# --- AC-EM008 inline CSS ------------------------------------------------------------------
def test_admin_html_gets_inline_defaults_and_authored_style_wins():
    out = inline_defaults('<p>a</p><p style="margin:0">b</p><table><tr><td>c</td></tr></table><a href="/x">l</a>', "#123456")
    assert '<p style="margin:0 0 16px 0;">a</p>' in out
    assert '<p style="margin:0 0 16px 0;margin:0">b</p>' in out
    assert "<td style=\"padding:8px 10px;" in out
    assert '<a href="/x" style="color:#123456;text-decoration:underline;">' in out


def test_layout_has_no_style_dependent_rules_except_media_query():
    r = _render()
    style = r.body_html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "@media only screen and (max-width: 620px)" in style
    # Everything visible is styled inline: every <td> in the layout carries a style.
    tds = re.findall(r"<td(?![^>]*style=)[^>]*>", r.body_html)
    assert tds == []


# --- AC-EM009 mobile -----------------------------------------------------------------------
def test_mobile_breakpoint_rules():
    html = _render().body_html
    for rule in (".em-card { width: 100% !important;", ".em-pad { padding-left: 20px !important;", ".em-btn-table { width: 100% !important; }", ".em-facts td { display: block !important;"):
        assert rule in html


# --- AC-EM010 text part ---------------------------------------------------------------------
def test_text_part_derived_in_block_order_and_link_not_duplicated():
    r = _render()
    t = r.body_text
    order = [t.index(s) for s in ("Hello Aina", "First para.", "Customer: Lim & Sons", "Open it: https://crm.example.com/x/1", "Custom x", "Acme Trading")]
    assert order == sorted(order)
    assert t.count("https://crm.example.com/x/1") == 2  # button + the custom html link, not the secondary link
    assert "<p>" not in t


def test_explicit_body_text_wins():
    r = _render(body_text="Plain {{ who }}")
    assert r.body_text == "Plain Aina"


def test_subject_rendered_single_line():
    r = _render(subject="A\n{{ who }}\n")
    assert r.subject == "A Aina"


# --- AC-EM011 unsafe urls ----------------------------------------------------------------------
def test_javascript_url_dropped():
    r = _render(_doc({"type": "button", "label": "x", "url": "javascript:alert(1)"}, {"type": "link", "url": "data:text/html,x"}))
    assert "javascript:" not in r.body_html and "data:text" not in r.body_html


def test_block_order_is_render_order():
    doc = _doc({"type": "footer"}, {"type": "heading", "text": "H"}, {"type": "brand_header"})
    html = _render(doc).body_html
    assert html.index("You received this email") < html.index(">H</h1>") < html.index("<img")


# --- theme ---------------------------------------------------------------------------------------
def test_theme_defaults_from_system_settings():
    t = _theme()
    assert t.company_name == "Acme Trading"
    assert t.logo_url == "https://cdn.example.com/logo.png"
    assert t.help_email == "help@acme.example"
    assert t.primary_color == "#2563EB" and t.button_color == "#2563EB"
    assert [s.label for s in t.social_links] == ["Facebook", "LinkedIn"]


def test_theme_defaults_logo_falls_back_to_company_logo():
    class NoLogo(_Settings):
        logo = None
    t = resolve_theme({}, theme_defaults(NoLogo(), "https://cdn.example.com/company.png"))
    assert t.logo_url == "https://cdn.example.com/company.png"


def test_stored_company_name_reaches_the_default_footer_note():
    """PR 1622 owner hand test: the live install's System Settings name is still the
    template's "Metronic"; the company named on the Email Theme screen must be the one the
    footer's default sentence names, not only the header."""
    t = _theme(company_name="Sorento")
    assert t.footer_note == (
        "You received this email because you have an account on Sorento. "
        "It is a service message about your work in the system."
    )


def test_stored_footer_note_still_wins_over_the_default():
    t = _theme(company_name="Sorento", footer_note="Custom note.")
    assert t.footer_note == "Custom note."


def test_stored_invalid_value_ignored_field_by_field(caplog):
    with caplog.at_level(logging.WARNING):
        t = _theme(primary_color="red", button_radius=99, company_name="Kept")
    assert t.primary_color == "#2563EB"
    assert t.button_radius == 8
    assert t.company_name == "Kept"
    assert "email theme field primary_color ignored" in caplog.text


@pytest.mark.parametrize("payload", [
    {"primary_color": "blue"},
    {"button_radius": 40},
    {"font": "comic"},
    {"logo_url": "ftp://x"},
    {"logo_alignment": "right"},
    {"social_links": [{"label": "x", "url": "javascript:1"}]},
    {"social_links": [{"label": f"s{i}", "url": "https://x"} for i in range(9)]},
])
def test_theme_validation_rejects(payload):
    with pytest.raises(Exception):
        EmailTheme.model_validate(payload)


def test_theme_short_hex_normalised():
    assert EmailTheme.model_validate({"primary_color": "#abc"}).primary_color == "#AABBCC"


def test_font_stack_applied():
    html = _render(theme=_theme(font="georgia")).body_html
    assert "font-family:Georgia,'Times New Roman',serif" in html


# --- implicit document ---------------------------------------------------------------------------
def test_null_layout_is_header_body_footer():
    doc = parse_document(None, "<p>Legacy {{ who }}</p>")
    assert [b.type for b in doc.blocks] == ["brand_header", "custom_text", "footer"]
    r = _render(doc)
    assert "Legacy Aina" in r.body_html and "<img" in r.body_html


def test_implicit_document_empty_body():
    assert [b.type for b in implicit_document("").blocks] == ["brand_header", "custom_text", "footer"]


# --- AC-EM080..082 fallback --------------------------------------------------------------------------
BROKEN = _doc(
    {"type": "brand_header"},
    {"type": "heading", "text": "Fine {{ who }}"},
    {"type": "custom_text", "html": "<p>{% if %}</p>"},
    {"type": "footer"},
)


def test_broken_block_sends_safe_layout_with_text_and_logs(caplog):
    with caplog.at_level(logging.ERROR):
        r = _render(BROKEN)
    assert r.fallback_used
    for part in (r.subject, r.body_html, r.body_text):
        assert "[template-error" not in part
    assert r.subject == "Subj Aina"
    assert "Fine Aina" in r.body_text
    assert "Fine Aina" in r.body_html
    assert has_layout(r.body_html)
    assert "email render failed" in caplog.text


def test_broken_subject_strips_jinja():
    r = _render(subject="Order {{ oops( }} ready")
    assert r.fallback_used
    assert r.subject == "Order ready"


def test_broken_subject_with_nothing_left():
    r = _render(subject="{{ oops( }}")
    assert r.subject == "Notification"


def test_broken_text_part_uses_blocks():
    r = _render(body_text="{% for %}")
    assert r.fallback_used
    assert "Hello Aina" in r.body_text
    assert "[template-error" not in r.body_text


def test_runtime_error_in_template_falls_back():
    r = _render(_doc({"type": "intro", "text": "{{ 1 / 0 }}"}, {"type": "heading", "text": "Still here"}))
    assert r.fallback_used and "Still here" in r.body_text


def test_broken_theme_sends_bare_html(monkeypatch):
    import app.services.email_layout as el

    def boom(**kw):
        raise RuntimeError("theme exploded")

    monkeypatch.setattr(el, "_layout", boom)
    r = _render()
    assert r.fallback_used
    assert has_layout(r.body_html)
    assert "Subj Aina" in r.body_html
    assert "[template-error" not in r.body_html


def test_block_document_validation_rejects_unknown_type():
    with pytest.raises(Exception):
        EmailDocument.model_validate({"version": 1, "blocks": [{"type": "hero_image"}]})


def test_protocol_relative_urls_dropped():  # security review N1
    r = _render(_doc({"type": "button", "label": "x", "url": "//evil.example/x"}, {"type": "link", "url": "/\\evil.example"}))
    assert "evil.example" not in r.body_html


def test_help_email_must_be_an_address():  # N4
    with pytest.raises(Exception):
        EmailTheme.model_validate({"help_email": "a@b.co?bcc=x@y.z"})
    assert EmailTheme.model_validate({"help_email": "help@acme.example"}).help_email == "help@acme.example"


def test_render_budget_stops_runaway_template_quickly():  # B2 / S1
    import time

    started = time.monotonic()
    r = _render(_doc({"type": "heading", "text": "{% for i in range(1000) %}{% for j in range(1000) %}{% for k in range(1000) %}{% endfor %}{% endfor %}{% endfor %}"}, {"type": "intro", "text": "Still sent"}))
    assert time.monotonic() - started < 6
    assert r.fallback_used and "Still sent" in r.body_text


def test_sla_digest_with_no_rows_keeps_empty_state():  # review S6
    from app.services.email_system_templates import SYSTEM_TEMPLATES

    t = SYSTEM_TEMPLATES["sla_daily_summary"]
    ctx = {**t.sample, "conversations": [], "outstanding_count": 0, "company": {"name": "Sorento"}}
    r = render_document(EmailDocument.model_validate(t.document()), subject=t.subject, context=ctx, theme=_theme(), preheader=t.preheader)
    assert not r.fallback_used
    assert "No outstanding assigned conversations." in r.body_html


def test_overlong_social_setting_is_skipped_not_raised():  # review nit
    class Long(_Settings):
        social_facebook = "https://facebook.com/" + "x" * 600
    t = resolve_theme({}, theme_defaults(Long()))
    assert [s.label for s in t.social_links] == ["LinkedIn"]
