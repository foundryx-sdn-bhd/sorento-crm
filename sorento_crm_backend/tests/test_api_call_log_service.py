"""api_call_log payload handling + call attribution.

Covers UAC OBS-S3-04, OBS-S3-05, OBS-S3-06.

Two things this table must never do: store a secret, or grow without bound. Both
are handled here rather than in the middleware so they are testable without an
ASGI round-trip, and so a future caller cannot bypass them by writing a row
directly.

Redaction is deliberately key-based and case-insensitive. Matching on *values*
(e.g. "looks like a token") is the tempting version and the wrong one - it fails
open on the secret you did not anticipate.
"""
import json

import pytest

from app.services.api_call_log_service import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_PAYLOAD_CHARS,
    sanitize_body,
    summarize_validation_errors,
    REDACTED,
    TRUNCATION_MARKER,
    classify_outcome,
    redact_mapping,
    resolve_source,
    truncate_payload,
)


# --------------------------------------------------------------------------- #
# Redaction                                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "key",
    ["x-api-key", "X-API-KEY", "authorization", "Authorization", "cookie", "x-auth-token"],
)
def test_secret_headers_are_redacted(key):
    out = redact_mapping({key: "super-secret-value", "content-type": "application/json"})
    assert out[key] == REDACTED
    assert out["content-type"] == "application/json"


@pytest.mark.parametrize("key", ["password", "api_key", "secret", "access_token", "refresh_token"])
def test_secret_body_keys_are_redacted(key):
    """The plan named headers; body keys carry secrets too and cost nothing to cover."""
    assert redact_mapping({key: "hunter2"})[key] == REDACTED


def test_redaction_preserves_non_secret_values():
    payload = {"contact_id": "abc", "message": "hello"}
    assert redact_mapping(payload) == payload


def test_redaction_is_recursive():
    """A secret one level down is still a secret."""
    out = redact_mapping({"outer": {"authorization": "Bearer xyz", "keep": 1}})
    assert out["outer"]["authorization"] == REDACTED
    assert out["outer"]["keep"] == 1


def test_redaction_walks_lists():
    out = redact_mapping({"items": [{"password": "p"}, {"ok": "v"}]})
    assert out["items"][0]["password"] == REDACTED
    assert out["items"][1]["ok"] == "v"


def test_redaction_does_not_mutate_the_caller_mapping():
    """The middleware redacts the SAME dict the request is still using."""
    original = {"authorization": "Bearer xyz"}
    redact_mapping(original)
    assert original["authorization"] == "Bearer xyz"


# --------------------------------------------------------------------------- #
# Truncation                                                                   #
# --------------------------------------------------------------------------- #
def test_large_payload_is_truncated_with_a_visible_marker():
    out = truncate_payload("x" * (MAX_PAYLOAD_CHARS * 2))
    assert len(out) <= MAX_PAYLOAD_CHARS + len(TRUNCATION_MARKER)
    assert out.endswith(TRUNCATION_MARKER)


def test_small_payload_is_untouched():
    assert truncate_payload("small") == "small"


def test_none_payload_stays_none():
    """An empty body must not be stored as the string 'None'."""
    assert truncate_payload(None) is None


def test_bytes_payload_is_decoded():
    assert truncate_payload(b'{"a": 1}') == '{"a": 1}'


def test_undecodable_bytes_do_not_raise():
    """A binary upload body must not 500 the request it is logging."""
    out = truncate_payload(b"\xff\xfe\x00binary")
    assert isinstance(out, str)


# --------------------------------------------------------------------------- #
# sanitize_body - what actually reaches the column                            #
# --------------------------------------------------------------------------- #
def test_json_body_secrets_are_redacted():
    """The path that matters: redact_mapping is useless if the middleware stores
    the raw bytes beside it."""
    body = json.dumps({"api_key": "sk-live-123", "contact_id": "abc"}).encode()
    out = sanitize_body(body)
    assert "sk-live-123" not in out
    assert "abc" in out


def test_nested_json_body_secrets_are_redacted():
    body = json.dumps({"payload": {"credentials": {"password": "hunter2"}}}).encode()
    assert "hunter2" not in sanitize_body(body)


def test_redaction_happens_before_truncation():
    """Truncating first can slice a secret in half and store the front of it,
    which is still a leak. The secret must be gone regardless of position."""
    body = json.dumps({"filler": "x" * (MAX_PAYLOAD_CHARS * 2), "password": "hunter2"})
    assert "hunter2" not in sanitize_body(body)


def test_non_json_body_is_stored_bounded():
    """Form-encoded / plain text has no structure to redact by key."""
    assert sanitize_body(b"plain text body") == "plain text body"


def test_empty_body_is_none_not_empty_string():
    assert sanitize_body(b"") is None
    assert sanitize_body(b"   ") is None


def test_json_array_body_is_handled():
    out = sanitize_body(json.dumps([{"token": "t"}, {"ok": 1}]).encode())
    assert "\"t\"" not in out
    assert "ok" in out


# --------------------------------------------------------------------------- #
# Source attribution                                                           #
# --------------------------------------------------------------------------- #
def test_mcp_header_is_honoured():
    assert resolve_source({"x-source": "mcp"}) == "mcp"


def test_source_is_case_insensitive_on_key_and_value():
    assert resolve_source({"X-Source": "MCP"}) == "mcp"


def test_missing_header_falls_back_to_unknown():
    """Not 'n8n'. Most external traffic IS n8n today, but recording an assumption
    as a fact is how the table stops being evidence."""
    assert resolve_source({}) == "unknown"


def test_unrecognised_source_is_kept_but_bounded():
    """A new caller should be visible, not silently relabelled 'unknown' - but it
    cannot be allowed to write 10KB into a 32-char column."""
    assert resolve_source({"x-source": "some-new-caller"}) == "some-new-caller"
    assert len(resolve_source({"x-source": "z" * 500})) <= 32


def test_blank_source_falls_back():
    assert resolve_source({"x-source": "   "}) == "unknown"


# --------------------------------------------------------------------------- #
# Outcome                                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code,expected", [
    (200, "success"), (201, "success"), (204, "success"),
    (400, "client_error"), (401, "client_error"), (404, "client_error"), (422, "client_error"),
    (500, "server_error"), (502, "server_error"),
])
def test_outcome_classification(code, expected):
    assert classify_outcome(code) == expected


def test_missing_status_is_a_server_error():
    """No status means the request died before producing one - that is our fault,
    not the caller's, and it must not land in the success bucket."""
    assert classify_outcome(None) == "server_error"


# --------------------------------------------------------------------------- #
# 422 summary (CHAT-HISTORY-SCHEMA)                                            #
# --------------------------------------------------------------------------- #
def test_a_422_body_is_summarised_to_its_offending_fields():
    """A validation refusal never reaches the handler, so this summary is the
    only CRM-side evidence naming what was wrong."""
    body = json.dumps(
        {
            "detail": [
                {"type": "missing", "loc": ["body", "message"], "msg": "Field required"},
                {
                    "type": "string_type",
                    "loc": ["body", "reply_to_message_id"],
                    "msg": "Input should be a valid string",
                    "input": 1791283416935171,
                },
            ]
        }
    )
    out = summarize_validation_errors(422, body.encode())
    assert out is not None
    assert "body.message: Field required" in out
    assert "body.reply_to_message_id: Input should be a valid string" in out


def test_only_a_422_with_the_validation_shape_is_summarised():
    assert summarize_validation_errors(201, b'{"id": 1}') is None
    assert summarize_validation_errors(422, None) is None
    assert summarize_validation_errors(422, b"not json") is None
    # A hand-rolled 422 carrying a plain string detail is stored exactly as before.
    assert summarize_validation_errors(422, b'{"detail": "nope"}') is None


def test_a_422_summary_is_bounded():
    detail = [
        {"type": "missing", "loc": ["body", f"field_{i}"], "msg": "Field required"}
        for i in range(400)
    ]
    out = summarize_validation_errors(422, json.dumps({"detail": detail}))
    assert out is not None
    assert len(out) <= MAX_ERROR_MESSAGE_CHARS + len(TRUNCATION_MARKER)
