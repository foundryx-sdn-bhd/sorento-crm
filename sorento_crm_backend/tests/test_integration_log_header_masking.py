"""`integration_logs.request_headers` must never store a credential.

Every external endpoint that logs its own call copied the same two lines:
`dict(request.headers)` and then `if "x-api-key" in headers: headers["x-api-key"]
= "***"`. Exactly one key was masked, so `authorization`, `cookie`,
`x-chatbot-retry-key` and `proxy-authorization` landed in the table in plaintext,
readable by anyone who can read integration logs - once per customer message,
forever, because `chatbot.turns` and its logs are never pruned. `X-API-Key` and
`Authorization: Bearer` are BOTH accepted by `get_current_user_or_api_key`, so
which one an n8n HTTP node is configured with decided whether the secret was
kept.

There is now one helper and five callers, and this file is its guardrail: the
denylist test, the case-insensitivity test (HTTP header names are
case-insensitive and Starlette lowercases them, but a caller may pass a plain
dict), the substring rule that catches a header nobody has invented yet, and a
scan asserting no endpoint has gone back to hand-rolling it.

Pure functions and a source scan: no database, no HTTP, CI-safe.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.integration_service import (
    MASKED_HEADER_VALUE,
    sanitize_request_headers,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_every_named_credential_header_is_masked() -> None:
    masked = sanitize_request_headers(
        {
            "x-api-key": "sk-live-1",
            "authorization": "Bearer eyJhb.secret",
            "proxy-authorization": "Basic abc",
            "cookie": "session=zzt",
            "set-cookie": "session=zzt",
            "x-chatbot-retry-key": "retry-secret",
        }
    )

    assert set(masked.values()) == {MASKED_HEADER_VALUE}


def test_a_header_that_merely_looks_like_a_credential_is_masked_too() -> None:
    """The denylist cannot name a header that does not exist yet, so the rule is
    also "the NAME says it is one"."""
    masked = sanitize_request_headers(
        {
            "x-webhook-secret": "s",
            "x-some-token": "t",
            "x-vendor-api-key": "k",
            "x-signing-Key": "k",
        }
    )

    assert set(masked.values()) == {MASKED_HEADER_VALUE}


def test_ordinary_headers_survive_unchanged() -> None:
    headers = {
        "content-type": "application/json",
        "content-length": "1234",
        "user-agent": "n8n",
        "x-request-id": "req-1",
        "host": "fe-sorento.foundryx.my",
    }

    assert sanitize_request_headers(headers) == headers


def test_the_match_is_case_insensitive_and_keeps_the_original_name() -> None:
    masked = sanitize_request_headers({"X-API-Key": "sk", "Authorization": "Bearer x"})

    assert masked == {"X-API-Key": MASKED_HEADER_VALUE, "Authorization": MASKED_HEADER_VALUE}


def test_a_missing_credential_header_adds_nothing() -> None:
    """Masking must not INVENT a key: an absent header stays absent, or every log
    row would claim a credential was sent."""
    assert sanitize_request_headers({"content-type": "application/json"}) == {
        "content-type": "application/json"
    }
    assert sanitize_request_headers({}) == {}


def test_no_endpoint_hand_rolls_the_mask_any_more() -> None:
    """The copy-paste this replaces, as a pattern: assigning a literal mask to a
    header key. A new external endpoint that reinvents it fails here rather than
    in a table nobody reads.

    AC-806: the scan reads `HAND_ROLLED_HEADER_MASK_PATTERNS`, so it guards the RULE
    (three shapes, every denylisted header) rather than the one instance of it the
    original hard-coded regex knew about.
    """
    from app.services.integration_service import HAND_ROLLED_HEADER_MASK_PATTERNS

    offenders: list[str] = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        source = path.read_text()
        if any(pattern.search(source) for pattern in HAND_ROLLED_HEADER_MASK_PATTERNS):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert offenders == [], (
        "these files mask headers by hand instead of calling "
        f"sanitize_request_headers: {offenders}"
    )


# =============================================================================
# S8a security nits (AC-806).
#
# RED-first: nothing below exists yet. `test_the_guardrail_catches_every_hand_rolled_shape`
# fails on ImportError (`HAND_ROLLED_HEADER_MASK_PATTERNS` is not exported from
# `app.services.integration_service` yet) - that is the intended shape of red, not a
# fixture bug. The behavioural test fails on an assertion mismatch: `sanitize_request_headers`
# does not yet mask `x-forwarded-authorization` because `_CREDENTIAL_NAME_PARTS` does not
# include `"auth"` (only `"key"`, `"token"`, `"secret"`), and the whole-name denylist only
# matches the literal `"authorization"` / `"proxy-authorization"` spellings.
# =============================================================================


def test_x_forwarded_authorization_is_masked() -> None:
    """`x-forwarded-authorization` is neither `authorization` nor `proxy-authorization`
    (the exact denylist entries) and contains none of `key` / `token` / `secret`, so it
    sails through today unmasked - a forwarded bearer token logged in plaintext."""
    masked = sanitize_request_headers({"x-forwarded-authorization": "Bearer secret-token"})
    assert masked == {"x-forwarded-authorization": MASKED_HEADER_VALUE}


def test_the_guardrail_catches_every_hand_rolled_shape(tmp_path) -> None:
    """AC-806: the guardrail must catch three hand-rolled shapes, not just the one
    bracket-assignment regex `test_no_endpoint_hand_rolls_the_mask_any_more` already
    scans for:

    1. bracket assignment - `headers["x-api-key"] = "***"` (already caught today);
    2. `.get` read into a log dict - `log["headers"] = {"x-api-key": headers.get("x-api-key")}`;
    3. a dict literal carrying the masked value directly - `headers = {"x-api-key": "***"}`.

    `HAND_ROLLED_HEADER_MASK_PATTERNS` is the ONE place these three shapes are declared, so
    both this file's synthetic check and a real-file scan use the same patterns (never two
    hand-maintained regexes drifting apart). It does not exist yet.
    """
    from app.services.integration_service import HAND_ROLLED_HEADER_MASK_PATTERNS

    synthetic_module = tmp_path / "zzt_synthetic_hand_rolled_masking.py"
    synthetic_module.write_text(
        "\n".join(
            [
                "def bracket_assignment(headers):",
                '    headers["x-api-key"] = "***"',
                "",
                "def get_into_log_dict(headers, log):",
                '    log["headers"] = {"x-api-key": headers.get("x-api-key")}',
                "",
                "def dict_literal_with_masked_value():",
                '    headers = {"x-api-key": "***"}',
                "    return headers",
                "",
            ]
        )
    )
    source = synthetic_module.read_text(encoding="utf-8")

    matched_lines = {
        line_no
        for pattern in HAND_ROLLED_HEADER_MASK_PATTERNS
        for match in pattern.finditer(source)
        for line_no in (source.count("\n", 0, match.start()) + 1,)
    }
    # Lines 2, 5 and 8 (1-indexed) are the three offending shapes above: `"\n".join`
    # over the ten entries puts entry i on line i + 1, so the `.get` shape (entry 4)
    # is line 5, not line 6.
    assert 2 in matched_lines, "bracket-assignment shape was not caught"
    assert 5 in matched_lines, "`.get` read into a log dict was not caught"
    assert 8 in matched_lines, "dict-literal-with-masked-value shape was not caught"


def test_a_credential_header_outside_the_denylist_is_masked_by_name_parts() -> None:
    """S8a hardening (AC-806): `x-custom-auth-token` is neither an exact `CREDENTIAL_HEADERS`
    entry nor one of the three regressions above - it survives on the NAME-PARTS rule alone
    (`_CREDENTIAL_NAME_PARTS` carries both `"auth"` and `"token"`, either is enough). Named
    for the exact header the S8a coder asked to see covered, distinct from
    `test_x_forwarded_authorization_is_masked` above (that one is caught by `"auth"` only)."""
    masked = sanitize_request_headers({"x-custom-auth-token": "sk-live-secret"})
    assert masked == {"x-custom-auth-token": MASKED_HEADER_VALUE}


def test_a_fourth_hand_rolled_shape_a_ternary_inside_a_comprehension_is_caught() -> None:
    """S8a hardening (AC-806): a FOURTH hand-rolled shape - a dict COMPREHENSION whose
    value is a ternary comparing the key to a credential header literal, e.g.::

        log["headers"] = {k: ("***" if k.lower() == "x-api-key" else v) for k, v in headers.items()}

    Found as a gap during this hardening pass (measured directly against the three
    declared patterns before writing this assertion: none matched - pattern 1 wants the
    credential name as a dict/bracket KEY on an assignment's left side, pattern 2 wants a
    `.get(` call, pattern 3 wants the masked value as a dict literal's VALUE; this shape
    has the credential name on the RIGHT of a `==` inside a ternary). Fixed concurrently
    in the same lane (a fourth pattern joined `HAND_ROLLED_HEADER_MASK_PATTERNS`, per its
    own comment: "a per-key mask chosen by comparing the key to a credential header name
    inside a ternary") - this is now the regression guard for that shape, not an open
    finding.
    """
    from app.services.integration_service import HAND_ROLLED_HEADER_MASK_PATTERNS

    source = (
        "def conditional_comprehension(headers, log):\n"
        '    log["headers"] = {k: ("***" if k.lower() == "x-api-key" else v) '
        "for k, v in headers.items()}\n"
    )

    caught = any(pattern.search(source) for pattern in HAND_ROLLED_HEADER_MASK_PATTERNS)
    assert caught, (
        "the ternary-in-comprehension shape is not caught by any of "
        "HAND_ROLLED_HEADER_MASK_PATTERNS's declared shapes"
    )


def test_archived_fulfilment_feedback_plan_no_longer_carries_a_real_phone_number() -> None:
    """AC-806: the archived plan leaked a real WhatsApp number
    (`documentation/plans/_archive/scm/PLAN-scm-fulfilment-feedback-p4.md`, "the Sorento
    workspace has NO WeChat channel" paragraph). Checked with the spacing collapsed so a
    respacing of the same ten digits does not slip the grep."""
    repo_root = BACKEND_ROOT.parent
    plan_path = (
        repo_root
        / "documentation"
        / "plans"
        / "_archive"
        / "scm"
        / "PLAN-scm-fulfilment-feedback-p4.md"
    )
    assert plan_path.is_file(), f"expected the archived plan at {plan_path}"
    collapsed = re.sub(r"[\s-]+", "", plan_path.read_text(encoding="utf-8"))
    assert "601100000524" not in collapsed, (
        "the archived plan still carries the real phone number (+60 11-0000 0524, any "
        "spacing) - it must be redacted or replaced with a placeholder"
    )
