"""A fake of the shared service (ss), installed at the httpx boundary.

Used by the IDEATION-IN-CRM gateway and portal-proxy tests. Not a test module (leading
underscore). It patches ``httpx.Client.send`` - the one method every sync ``httpx.Client``
request (``.get``, ``.post``, ``.request``, ``.stream``) funnels through - so NO real network
call is possible and the production code is free to build its client however it likes, as
long as it is a SYNC ``httpx.Client`` (the contract the tests pin, see
``test_ideation_gateway.py`` module docstring).

ss base URL is ``https://shared.test/be``; the ``/be`` prefix is stripped so routes are
registered by their ss path (``/embed/ideas``, ``/public/ideas/{token}``).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx

SS_BASE = "https://shared.test/be"
SS_FE = "https://shared.test"
SIGNING_SECRET = "zzt-ideation-gateway-signing-secret"
CONNECTION_ID = "e5407a68-13ff-59f6-a337-408b46ca369b"


def configure_embed_settings(monkeypatch) -> None:
    """Point the (.env fallback) embed config at the fake ss."""
    from app.config import settings

    monkeypatch.setattr(settings, "ideation_shared_service_url", SS_BASE)
    monkeypatch.setattr(settings, "ideation_embed_fe_base_url", SS_FE)
    monkeypatch.setattr(settings, "ideation_embed_signing_secret", SIGNING_SECRET)
    monkeypatch.setattr(settings, "ideation_embed_connection_id", CONNECTION_ID)


def blank_embed_settings(monkeypatch) -> None:
    from app.config import settings

    for name in (
        "ideation_shared_service_url",
        "ideation_embed_fe_base_url",
        "ideation_embed_signing_secret",
        "ideation_embed_connection_id",
    ):
        monkeypatch.setattr(settings, name, None)


class FakeSS:
    def __init__(self) -> None:
        #: every non-session request: method, path, query (dict), json, content, headers
        self.calls: list[dict[str, Any]] = []
        #: the JSON body of every POST /embed/session
        self.session_calls: list[dict[str, Any]] = []
        #: every embed token handed out, in order
        self.tokens: list[str] = []
        #: (METHOD, ss path) -> Response kwargs dict or callable(request) -> httpx.Response
        self.routes: dict[tuple[str, str], Any] = {}
        self.session_expires_in = 300
        self.session_raises: Optional[Exception] = None
        self.session_status = 200
        self.call_raises: Optional[Exception] = None
        self._reject = 0

    # -- scripting ------------------------------------------------------------
    def route(self, method: str, path: str, *, status: int = 200, json_body: Any = None,
              content: Optional[bytes] = None, headers: Optional[dict] = None) -> None:
        self.routes[(method.upper(), path)] = {
            "status": status, "json": json_body, "content": content, "headers": headers or {},
        }

    def reject_next(self, n: int) -> None:
        """The next ``n`` non-session calls answer 401 (an expired / revoked embed token)."""
        self._reject = n

    # -- boundary -------------------------------------------------------------
    def install(self, monkeypatch) -> "FakeSS":
        from starlette.testclient import TestClient

        fake = self
        real_send = httpx.Client.send

        def _send(client_self, request, **kw):  # noqa: ANN001
            # Starlette's TestClient is itself an httpx.Client: the test's own calls into the
            # app must pass through, only the app's OUTBOUND calls are faked.
            if isinstance(client_self, TestClient):
                return real_send(client_self, request, **kw)
            return fake.handle(request)

        monkeypatch.setattr(httpx.Client, "send", _send)
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/be"):
            path = path[len("/be"):]
        body = request.read()

        if path == "/embed/session" and request.method == "POST":
            self.session_calls.append(json.loads(body or b"{}"))
            if self.session_raises is not None:
                raise self.session_raises
            if self.session_status != 200:
                return httpx.Response(self.session_status, json={"error": {"message": "nope"}}, request=request)
            token = f"ZZTEMBEDTOKEN{len(self.tokens) + 1}xq"
            self.tokens.append(token)
            expires = datetime.now(timezone.utc) + timedelta(seconds=self.session_expires_in)
            return httpx.Response(
                200, json={"token": token, "expires_at": expires.isoformat()}, request=request
            )

        parsed_json = None
        if body and "application/json" in request.headers.get("content-type", ""):
            parsed_json = json.loads(body)
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "query": dict(request.url.params),
                "json": parsed_json,
                "content": body,
                "headers": {k.lower(): v for k, v in request.headers.items()},
            }
        )
        if self.call_raises is not None:
            raise self.call_raises

        if path.startswith("/embed/") and self._reject > 0:
            self._reject -= 1
            return httpx.Response(
                401, json={"error": {"code": "invalid_token", "message": "Invalid embed token."}}, request=request
            )

        spec = self.routes.get((request.method, path))
        if callable(spec):
            return spec(request)
        if spec is None:
            return httpx.Response(200, json={}, request=request)
        kwargs: dict[str, Any] = {"headers": spec["headers"], "request": request}
        if spec["content"] is not None:
            kwargs["content"] = spec["content"]
        elif spec["json"] is not None:
            kwargs["json"] = spec["json"]
        return httpx.Response(spec["status"], **kwargs)


def message_of(resp) -> str:
    """Every human message in a CRM error body, joined with " | ".

    The app has more than one error envelope (HTTPException ``detail`` string, AppException
    ``{message, detail, code}``, ss-style ``{error: {message}}``). Tests assert the expected text
    is IN this string, so they pin the wording and not the envelope.
    """
    data = resp.json()
    found: list[str] = []
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            found.append(detail)
        elif isinstance(detail, dict):
            found += [str(detail[k]) for k in ("message", "detail") if detail.get(k)]
        if isinstance(data.get("message"), str):
            found.append(data["message"])
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            found.append(str(err["message"]))
    return " | ".join(found)
