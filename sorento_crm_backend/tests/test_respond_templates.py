"""Respond.io WhatsApp template sync + 24h-window-aware sending
(PLAN-whatsapp-template-fallback):

- sync_templates: upsert by natural key, hard-delete rows gone from the API,
  idempotent re-run, body_text/param_count derivation, only-approved usable
- set/clear default: approval gate, full-param-mapping gate, variable
  validation, stale-key trimming, is_valid + template_removed flags
- get_window_state: open (recent incoming) vs closed (old / none),
  Respond-API-error -> chat_history fallback -> none -> closed, 23h margin
- sanitize_param: newlines -> " | ", whitespace collapse, truncation
- send_text_or_template: open -> plain text; closed -> default template;
  closed + no valid default -> TemplateSendSkipped

Run with:
    pytest tests/test_respond_templates.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.access import RespondContact
from app.models.chat_history import ChatHistory
from app.models.respond_template import (
    RespondChannel,
    RespondMessageTemplate,
    RespondTemplateDefault,
)
from app.models.respond_workspace import RespondWorkspace
from tests._pg_fixture import blank_session

WORKSPACE_ID = str(uuid.uuid4())
CHANNEL_RID = 453209


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def workspace(db):
    ws = RespondWorkspace(
        id=WORKSPACE_ID,
        space_id="364817",
        name="Sorento",
        api_key_ciphertext="not-encrypted-test",
        is_active=True,
        is_default=True,
    )
    db.add(ws)
    db.commit()
    return ws


def _api_template(rid, name, status="approved", body="Hi {{1}}, update: {{2}}", params=2):
    return {
        "id": rid,
        "name": name,
        "languageCode": "en",
        "category": "UTILITY",
        "status": status,
        "statusDetail": None,
        "templateId": f"meta-{rid}",
        "namespace": None,
        "components": [{"type": "body", "text": body}],
    }


def _mock_client(channels, templates_by_channel):
    client = MagicMock()
    client.list_channels.return_value = channels
    client.list_message_templates.side_effect = lambda cid: templates_by_channel.get(int(cid), [])
    return client


# --------------------------------------------------------------------------- sync


def test_sync_inserts_channels_and_templates(db, workspace):
    from app.services import respond_template_service as svc

    client = _mock_client(
        [{"id": CHANNEL_RID, "name": "WA", "source": "whatsapp_business"}],
        {CHANNEL_RID: [_api_template(1, "update"), _api_template(2, "promo", status="rejected")]},
    )
    with patch.object(svc, "_client_for_workspace", return_value=client):
        result = svc.sync_templates(db)

    assert result == {"synced": 2, "deleted": 0, "channels": 1}
    rows = db.query(RespondMessageTemplate).all()
    assert {r.name for r in rows} == {"update", "promo"}
    upd = next(r for r in rows if r.name == "update")
    assert upd.param_count == 2
    assert upd.body_text.startswith("Hi {{1}}")


def test_sync_is_idempotent_and_hard_deletes_gone_rows(db, workspace):
    from app.services import respond_template_service as svc

    client = _mock_client(
        [{"id": CHANNEL_RID, "name": "WA", "source": "whatsapp_business"}],
        {CHANNEL_RID: [_api_template(1, "update"), _api_template(2, "promo")]},
    )
    with patch.object(svc, "_client_for_workspace", return_value=client):
        svc.sync_templates(db)
        # second run unchanged -> still 2 rows, none duplicated
        svc.sync_templates(db)
    assert db.query(RespondMessageTemplate).count() == 2

    # template 2 disappears from the API -> hard-deleted
    client2 = _mock_client(
        [{"id": CHANNEL_RID, "name": "WA", "source": "whatsapp_business"}],
        {CHANNEL_RID: [_api_template(1, "update")]},
    )
    with patch.object(svc, "_client_for_workspace", return_value=client2):
        result = svc.sync_templates(db)
    assert result["deleted"] == 1
    assert {r.name for r in db.query(RespondMessageTemplate).all()} == {"update"}


# --------------------------------------------------------------------------- defaults


def _seed_channel_with_template(db, *, name="update", status="approved", param_count=2):
    ch = RespondChannel(id=str(uuid.uuid4()), workspace_id=WORKSPACE_ID, respond_channel_id=CHANNEL_RID)
    db.add(ch)
    db.flush()
    tpl = RespondMessageTemplate(
        id=str(uuid.uuid4()),
        channel_id=ch.id,
        respond_template_id=1,
        name=name,
        language_code="en",
        status=status,
        components=[{"type": "body", "text": "Hi {{1}}, {{2}}"}],
        body_text="Hi {{1}}, {{2}}",
        param_count=param_count,
    )
    db.add(tpl)
    db.commit()
    return ch, tpl


def test_set_default_requires_full_param_mapping(db, workspace):
    from app.services import respond_template_service as svc
    from app.services.error_handler import AppException

    _, tpl = _seed_channel_with_template(db)
    with pytest.raises(AppException):
        svc.set_default(db, "complaint", template_id=str(tpl.id), param_mapping={"1": "contact_name"})


def test_set_default_rejects_unapproved_template(db, workspace):
    from app.services import respond_template_service as svc
    from app.services.error_handler import AppException

    _, tpl = _seed_channel_with_template(db, status="rejected")
    with pytest.raises(AppException):
        svc.set_default(
            db, "complaint", template_id=str(tpl.id),
            param_mapping={"1": "contact_name", "2": "message"},
        )


def test_set_default_rejects_unknown_variable(db, workspace):
    from app.services import respond_template_service as svc
    from app.services.error_handler import AppException

    _, tpl = _seed_channel_with_template(db)
    with pytest.raises(AppException):
        svc.set_default(
            db, "complaint", template_id=str(tpl.id),
            param_mapping={"1": "contact_name", "2": "bogus_var"},
        )


def test_set_default_portal_otp_requires_otp_code(db, workspace):
    # OTP template that covers every slot but never maps the code → rejected,
    # else a misconfigured OTP would ship a login message with no code.
    from app.services import respond_template_service as svc
    from app.services.error_handler import AppException

    _, tpl = _seed_channel_with_template(db)
    with pytest.raises(AppException):
        svc.set_default(
            db, "portal_otp", template_id=str(tpl.id),
            param_mapping={"1": "contact_name", "2": "message"},
        )


def test_set_default_portal_otp_accepts_when_code_mapped(db, workspace):
    from app.services import respond_template_service as svc

    _, tpl = _seed_channel_with_template(db)
    out = svc.set_default(
        db, "portal_otp", template_id=str(tpl.id),
        param_mapping={"1": "otp_code", "2": "message"},
    )
    assert out["is_valid"] is True
    assert "otp_code" in out["param_mapping"].values()


def test_set_default_happy_path_and_is_valid(db, workspace):
    from app.services import respond_template_service as svc

    _, tpl = _seed_channel_with_template(db)
    out = svc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message", "3": "status"},  # stale key trimmed
    )
    assert out["is_valid"] is True
    assert out["param_mapping"] == {"1": "contact_name", "2": "message"}


def test_default_flags_template_removed_after_sync_delete(db, workspace):
    from app.services import respond_template_service as svc

    _, tpl = _seed_channel_with_template(db)
    svc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message"},
    )
    # Simulate sync hard-deleting the template (FK SET NULL keeps the default row).
    db.query(RespondTemplateDefault).filter_by(use_case="complaint").update(
        {"template_id": None}
    )
    db.commit()
    row = svc.get_default_row(db, "complaint")
    serialized = svc.serialize_default("complaint", row)
    assert serialized["is_valid"] is False
    assert serialized["template_removed"] is True
    assert serialized["template_name"] == "update"  # snapshot survives


# --------------------------------------------------------------------------- window


def test_window_open_for_recent_incoming(db):
    from app.services import respond_messaging_service as svc

    recent_ms = int((datetime.utcnow() - timedelta(hours=2)).timestamp() * 1000)
    client = MagicMock()
    client.list_messages.return_value = {
        "items": [{"traffic": "incoming", "status": [{"timestamp": recent_ms}]}]
    }
    with patch("app.services.integration_service.RespondClient", return_value=client):
        state = svc.get_window_state(db, "id:123")
    assert state["open"] is True


def test_window_closed_for_old_incoming(db):
    from app.services import respond_messaging_service as svc

    old_ms = int((datetime.utcnow() - timedelta(hours=30)).timestamp() * 1000)
    client = MagicMock()
    client.list_messages.return_value = {
        "items": [{"traffic": "incoming", "status": [{"timestamp": old_ms}]}]
    }
    with patch("app.services.integration_service.RespondClient", return_value=client):
        state = svc.get_window_state(db, "id:123")
    assert state["open"] is False


def test_window_closed_when_no_incoming(db):
    from app.services import respond_messaging_service as svc

    client = MagicMock()
    client.list_messages.return_value = {"items": [{"traffic": "outgoing", "status": []}]}
    with patch("app.services.integration_service.RespondClient", return_value=client):
        state = svc.get_window_state(db, "id:123")
    assert state["open"] is False
    assert state["last_incoming_at"] is None


def test_window_falls_back_to_chat_history_on_api_error(db):
    from app.services import respond_messaging_service as svc

    # chat_histories.contact_id holds the Respond.io id, NOT the internal
    # respond_contacts UUID - every one of the 1,519 production rows is numeric
    # and joins on respond_io_id. Seeding a UUID here made the fallback lookup
    # miss, so this test used to assert the pre-fix behaviour (window always
    # CLOSED on API error) rather than the fix.
    respond_io_id = "123"
    contact_id = str(uuid.uuid4())
    db.add(
        ChatHistory(
            id=1,
            channel="whatsapp",
            contact_id=respond_io_id,
            phone_number="+60123",
            message="hi",
            sent_at=datetime.utcnow() - timedelta(hours=1),
            type="incoming",
        )
    )
    db.commit()

    client = MagicMock()
    client.list_messages.side_effect = RuntimeError("boom")
    with patch("app.services.integration_service.RespondClient", return_value=client):
        state = svc.get_window_state(db, f"id:{respond_io_id}", respond_contact_id=contact_id)
    assert state["open"] is True
    assert state["source"] == "chat_history"


# --------------------------------------------------------------------------- sanitize


def test_sanitize_param_flattens_newlines_and_collapses_space():
    from app.services.respond_messaging_service import sanitize_param

    out = sanitize_param("Status: approved\nReason:   damaged\n\nPortal: x")
    assert "\n" not in out
    # Newlines collapse to a single space (was " | " before the URL-button change).
    assert " | " not in out
    assert out == "Status: approved Reason: damaged Portal: x"
    assert "   " not in out


def test_sanitize_param_truncates():
    from app.services.respond_messaging_service import sanitize_param

    out = sanitize_param("a" * 2000, max_len=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_sanitize_param_empty_is_dash():
    from app.services.respond_messaging_service import sanitize_param

    assert sanitize_param("") == "-"
    assert sanitize_param(None) == "-"


# --------------------------------------------------------------------------- branch


def test_send_text_when_window_open(db):
    from app.services import respond_messaging_service as svc

    client = MagicMock()
    client.send_message.return_value = {"messageId": 1}
    with patch("app.services.integration_service.RespondClient", return_value=client), patch.object(
        svc, "get_window_state", return_value={"open": True, "last_incoming_at": None, "checked_at": "", "source": "x"}
    ):
        result = svc.send_text_or_template(
            db, identifier="id:1", text="hello", use_case="complaint"
        )
    assert result["sent_as"] == "text"
    client.send_message.assert_called_once()


def test_send_template_when_window_closed(db, workspace):
    from app.services import respond_messaging_service as svc
    from app.services import respond_template_service as tsvc

    _, tpl = _seed_channel_with_template(db)
    tsvc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message"},
    )

    client = MagicMock()
    client.send_template_message.return_value = {"messageId": 9}
    with patch("app.services.integration_service.RespondClient", return_value=client), patch.object(
        svc, "get_window_state", return_value={"open": False, "last_incoming_at": None, "checked_at": "", "source": "x"}
    ):
        result = svc.send_text_or_template(
            db, identifier="id:1", text="Your complaint is approved",
            use_case="complaint", context_vars={"contact_name": "Ms Ang"},
        )
    assert result["sent_as"] == "template"
    client.send_template_message.assert_called_once()
    kwargs = client.send_template_message.call_args.kwargs
    assert kwargs["channel_id"] == CHANNEL_RID
    # message var defaults to the composed text
    assert "Your complaint is approved" in kwargs["parameters"][1]


def test_closed_window_without_default_raises_skipped(db, workspace):
    from app.services import respond_messaging_service as svc

    client = MagicMock()
    with patch("app.services.integration_service.RespondClient", return_value=client), patch.object(
        svc, "get_window_state", return_value={"open": False, "last_incoming_at": None, "checked_at": "", "source": "x"}
    ):
        with pytest.raises(svc.TemplateSendSkipped):
            svc.send_text_or_template(
                db, identifier="id:1", text="hello", use_case="complaint"
            )


# --------------------------------------------------------------------------- rbac


def test_template_permissions_registered():
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    slugs = {e["slug"] for e in PERMISSION_REGISTRY}
    assert {
        "integration.respond_templates.view",
        "integration.respond_templates.edit",
        "integration.respond_templates.sync",
    } <= slugs


def test_send_template_message_payload_shape():
    """The send payload matches what the live Respond.io API accepts
    (verified 2026-06-08): channelId + message.type=whatsapp_template +
    template.components[body].text + positional parameters."""
    from app.services.integration_service import RespondClient

    captured = {}

    class _Resp:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    with patch("app.services.integration_service.httpx.Client", return_value=_Client()), patch.object(
        RespondClient, "__init__", lambda self: setattr(self, "base_url", "https://api.respond.io") or setattr(self, "api_key", "k") or setattr(self, "space_id", None)
    ):
        RespondClient().send_template_message(
            "id:1",
            channel_id=CHANNEL_RID,
            template_name="update",
            language_code="en",
            body_text="Hi {{1}}, {{2}}",
            parameters=["Ms Ang", "approved"],
        )

    payload = captured["json"]
    assert payload["channelId"] == CHANNEL_RID
    assert payload["message"]["type"] == "whatsapp_template"
    tpl = payload["message"]["template"]
    assert tpl["name"] == "update"
    assert tpl["languageCode"] == "en"
    body = tpl["components"][0]
    assert body["type"] == "body"
    assert body["text"] == "Hi {{1}}, {{2}}"
    assert [p["text"] for p in body["parameters"]] == ["Ms Ang", "approved"]


# --------------------------------------------------- dynamic URL button (2026-06-22)


_BUTTON_COMPONENTS = [
    {"type": "body", "text": "Hi {{1}}, update: {{2}}"},
    {
        "type": "buttons",
        "buttons": [
            {
                "type": "url",
                "text": "View complaint",
                "url": "https://fe-sorento.foundryx.my/{{1}}",
                "parameters": [{"type": "text", "text": ""}],
            }
        ],
    },
]


def _seed_button_template(db, *, status="approved"):
    ch = RespondChannel(id=str(uuid.uuid4()), workspace_id=WORKSPACE_ID, respond_channel_id=CHANNEL_RID)
    db.add(ch)
    db.flush()
    tpl = RespondMessageTemplate(
        id=str(uuid.uuid4()),
        channel_id=ch.id,
        respond_template_id=2,
        name="update_with_button",
        language_code="en",
        status=status,
        components=_BUTTON_COMPONENTS,
        body_text="Hi {{1}}, update: {{2}}",
        param_count=2,
    )
    db.add(tpl)
    db.commit()
    return ch, tpl


_STRUCTURED_BODY = (
    "Hi {{1}},\n\nProject: {{2}}\nCustomer: {{3}}\nEnquiry number: {{4}}\n"
    "Delivery Order: {{5}}\n\nUpdate: {{6}}\n\nThis is a system-generated message."
)
_STRUCTURED_COMPONENTS = [
    {"type": "body", "text": _STRUCTURED_BODY},
    {
        "type": "buttons",
        "buttons": [
            {
                "type": "url",
                "text": "Click to View",
                "url": "https://fe-sorento.foundryx.my/{{1}}",
                "parameters": [{"type": "text", "text": ""}],
            }
        ],
    },
]


def _seed_structured_complaint_template(db, *, status="approved"):
    ch = RespondChannel(id=str(uuid.uuid4()), workspace_id=WORKSPACE_ID, respond_channel_id=CHANNEL_RID)
    db.add(ch)
    db.flush()
    tpl = RespondMessageTemplate(
        id=str(uuid.uuid4()),
        channel_id=ch.id,
        respond_template_id=3,
        name="complaint_update_structured",
        language_code="en",
        status=status,
        components=_STRUCTURED_COMPONENTS,
        body_text=_STRUCTURED_BODY,
        param_count=6,
    )
    db.add(tpl)
    db.commit()
    return ch, tpl


def test_complaint_structured_template_send_payload(db, workspace):
    """PLAN-complaint-structured-update-template: complaint send fills 6 discrete
    slots (Project / Customer / Enquiry / DO / bare Update), emits the URL button
    with the link suffix, no verbose run-on, and the link is not duplicated in the
    body (the button carries it)."""
    from app.services import respond_messaging_service as svc
    from app.services import respond_template_service as tsvc

    _, tpl = _seed_structured_complaint_template(db)
    tsvc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={
            "1": "contact_name", "2": "project", "3": "customer",
            "4": "entity_number", "5": "delivery_order", "6": "update",
            "button_url": "portal_url",
        },
    )

    link = "https://fe-sorento.foundryx.my/portal/c/A/complaint/x"
    client = MagicMock()
    client.send_template_message.return_value = {"messageId": 9}
    with patch("app.services.integration_service.RespondClient", return_value=client):
        result = svc.send_template_for_use_case(
            db, identifier="id:1", use_case="complaint",
            context_vars={
                "contact_name": "Jay Tan",
                "project": "Sunway Tower",
                "customer": "Acme Sdn Bhd",
                "entity_number": "CMP2026-0011",
                "delivery_order": "PS202605-0473",
                "update": "Status changed to approved.",
                "portal_url": link,
            },
        )
    params = result["params"]
    assert params == ["Jay Tan", "Sunway Tower", "Acme Sdn Bhd", "CMP2026-0011",
                      "PS202605-0473", "Status changed to approved."]
    assert " | " not in " ".join(params)
    assert all(link not in p for p in params)  # link not duplicated in body params
    assert result["button"]["suffix"] == "portal/c/A/complaint/x"
    assert result["button"]["url"] == "https://fe-sorento.foundryx.my/{{1}}"


def test_complaint_structured_template_empty_fields_fill_dash(db, workspace):
    """D3: blank Project / DO fill as '-' (WhatsApp params can't be empty)."""
    from app.services import respond_messaging_service as svc
    from app.services import respond_template_service as tsvc

    _, tpl = _seed_structured_complaint_template(db)
    tsvc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={
            "1": "contact_name", "2": "project", "3": "customer",
            "4": "entity_number", "5": "delivery_order", "6": "update",
            "button_url": "portal_url",
        },
    )
    client = MagicMock()
    client.send_template_message.return_value = {"messageId": 9}
    with patch("app.services.integration_service.RespondClient", return_value=client):
        result = svc.send_template_for_use_case(
            db, identifier="id:1", use_case="complaint",
            context_vars={
                "contact_name": "Jay Tan", "entity_number": "CMP2026-0011",
                "update": "sdaftest",
                "portal_url": "https://fe-sorento.foundryx.my/x",
            },
        )
    # project (slot2) + customer (slot3) + delivery_order (slot5) missing -> '-'
    assert result["params"] == ["Jay Tan", "-", "-", "CMP2026-0011", "-", "sdaftest"]


def test_url_button_detection_and_base():
    from app.services import respond_template_service as svc

    btn = svc.url_button_of(_BUTTON_COMPONENTS)
    assert btn is not None
    assert svc.button_url_base(btn) == "https://fe-sorento.foundryx.my/"
    # Static button (no {{n}}) is ignored - nothing to fill.
    static = [{"type": "buttons", "buttons": [{"type": "url", "url": "https://x/"}]}]
    assert svc.url_button_of(static) is None


def test_set_default_requires_button_url_mapping(db, workspace):
    from app.services import respond_template_service as svc
    from app.services.error_handler import AppException

    _, tpl = _seed_button_template(db)
    # Body slots mapped but button link missing -> rejected.
    with pytest.raises(AppException):
        svc.set_default(
            db, "complaint", template_id=str(tpl.id),
            param_mapping={"1": "contact_name", "2": "message"},
        )


def test_set_default_persists_button_url_key(db, workspace):
    from app.services import respond_template_service as svc

    _, tpl = _seed_button_template(db)
    out = svc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message", "button_url": "portal_url"},
    )
    assert out["is_valid"] is True
    assert out["has_url_button"] is True
    assert out["button_url_base"] == "https://fe-sorento.foundryx.my/"
    assert out["param_mapping"]["button_url"] == "portal_url"


def test_set_default_drops_button_url_when_no_button(db, workspace):
    from app.services import respond_template_service as svc

    _, tpl = _seed_channel_with_template(db)  # plain body, no button
    out = svc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message", "button_url": "portal_url"},
    )
    assert "button_url" not in out["param_mapping"]
    assert out["has_url_button"] is False


def test_closed_window_send_fills_button_suffix_and_strips_body_url(db, workspace):
    from app.services import respond_messaging_service as svc
    from app.services import respond_template_service as tsvc

    _, tpl = _seed_button_template(db)
    tsvc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message", "button_url": "portal_url"},
    )

    portal = "https://fe-sorento.foundryx.my/portal/c/ABC123/complaint/xyz"
    text = f"There has been an update: leak fixed.\n\n{portal}"

    client = MagicMock()
    client.send_template_message.return_value = {"messageId": 11}
    with patch("app.services.integration_service.RespondClient", return_value=client), patch.object(
        svc, "get_window_state", return_value={"open": False, "last_incoming_at": None, "checked_at": "", "source": "x"}
    ):
        result = svc.send_text_or_template(
            db, identifier="id:1", text=text, use_case="complaint",
            context_vars={"contact_name": "CONTACT A"},
        )
    assert result["sent_as"] == "template"
    kwargs = client.send_template_message.call_args.kwargs
    # Suffix = portal URL minus the button's static base.
    assert kwargs["button"]["suffix"] == "portal/c/ABC123/complaint/xyz"
    assert kwargs["button"]["url"] == "https://fe-sorento.foundryx.my/{{1}}"
    # The portal URL is stripped from the body param (now carried by the button).
    assert portal not in kwargs["parameters"][1]
    assert "leak fixed" in kwargs["parameters"][1]


def test_in_window_complaint_button_template_renders_structured_body(db, workspace):
    """D1 (2026-06-23): for the complaint use_case, in-window renders the SAME
    structured template body as out-of-window (cross-window uniformity), then
    appends the link inline (free text has no button). Other button use-cases
    still send raw free text - see the next test."""
    from app.services import respond_messaging_service as svc
    from app.services import respond_template_service as tsvc

    _, tpl = _seed_button_template(db)
    tsvc.set_default(
        db, "complaint", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "update", "button_url": "portal_url"},
    )

    link = "https://fe-sorento.foundryx.my/portal/c/A/complaint/x"
    client = MagicMock()
    client.send_message.return_value = {"messageId": 12}
    with patch("app.services.integration_service.RespondClient", return_value=client), patch.object(
        svc, "get_window_state", return_value={"open": True, "last_incoming_at": None, "checked_at": "", "source": "x"}
    ):
        result = svc.send_text_or_template(
            db, identifier="id:1", text="ignored fallback", use_case="complaint",
            context_vars={"contact_name": "CONTACT A", "update": "Status changed to approved.", "portal_url": link},
        )
    assert result["sent_as"] == "text"  # in-window is always a free-text send
    sent_text = client.send_message.call_args.args[1]
    # Filled structured body, then the link appended inline.
    assert sent_text == f"Hi CONTACT A, update: Status changed to approved.\n\n{link}"
    client.send_template_message.assert_not_called()


def test_in_window_noncomplaint_button_template_sends_raw_text(db, workspace):
    """Decouple still holds for non-complaint button use-cases: in-window sends the
    rich free text, not the short template body (buttons don't render in free text;
    URL stays clickable inline)."""
    from app.services import respond_messaging_service as svc
    from app.services import respond_template_service as tsvc

    _, tpl = _seed_button_template(db)
    tsvc.set_default(
        db, "stock_inquiry", template_id=str(tpl.id),
        param_mapping={"1": "contact_name", "2": "message", "button_url": "portal_url"},
    )

    rich = "Full multi-line\nupdate with link https://fe-sorento.foundryx.my/portal/c/A/si/x"
    client = MagicMock()
    client.send_message.return_value = {"messageId": 12}
    with patch("app.services.integration_service.RespondClient", return_value=client), patch.object(
        svc, "get_window_state", return_value={"open": True, "last_incoming_at": None, "checked_at": "", "source": "x"}
    ):
        result = svc.send_text_or_template(
            db, identifier="id:1", text=rich, use_case="stock_inquiry",
            context_vars={"contact_name": "CONTACT A"},
        )
    assert result["sent_as"] == "text"
    sent_text = client.send_message.call_args.args[1]
    assert sent_text == rich  # raw, newlines preserved
    client.send_template_message.assert_not_called()


def test_send_template_message_payload_includes_button_component():
    from app.services.integration_service import RespondClient

    captured = {}

    class _Resp:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            captured["json"] = json
            return _Resp()

    with patch("app.services.integration_service.httpx.Client", return_value=_Client()), patch.object(
        RespondClient, "__init__", lambda self: setattr(self, "base_url", "https://api.respond.io") or setattr(self, "api_key", "k") or setattr(self, "space_id", None)
    ):
        RespondClient().send_template_message(
            "id:1",
            channel_id=CHANNEL_RID,
            template_name="update_with_button",
            language_code="en",
            body_text="Hi {{1}}, update: {{2}}",
            parameters=["CONTACT A", "leak fixed"],
            button={
                "text": "View complaint",
                "url": "https://fe-sorento.foundryx.my/{{1}}",
                "suffix": "portal/c/A/complaint/x",
            },
        )

    comps = captured["json"]["message"]["template"]["components"]
    assert comps[0]["type"] == "body"
    btn_comp = comps[1]
    assert btn_comp["type"] == "buttons"
    btn = btn_comp["buttons"][0]
    assert btn["type"] == "url"
    assert btn["text"] == "View complaint"
    assert btn["url"] == "https://fe-sorento.foundryx.my/{{1}}"
    assert btn["parameters"][0]["text"] == "portal/c/A/complaint/x"
