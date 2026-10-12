"""Portal AI extract runs off the event loop
(documentation/plans/ai-extract/PLAN-ai-extract-off-the-loop.md,
documentation/plans/ai-extract/ai-extract-off-the-loop-acceptance-criteria.md).

`app/api/v1/public/ai_extract.py::ai_extract` used to be `async def` and called
the synchronous `AIExtractService.extract` (PDF render plus LLM round trip,
measured 5.8 to 9.8 s per image) inline, on the event loop, freezing every
other concurrent request on that gunicorn worker. Same class of defect fixed
for the dealer-kit flyer read in PR #164
(`documentation/plans/dealer-kit/PLAN-flyer-read-hardening.md`).

* AC-1 - the extract call runs off the event loop (pinned by shape, not a
  wall clock).
* AC-2 - the loop stays responsive to a second request while an extract is in
  flight, proven by request ORDERING rather than elapsed time.
* AC-3 - no route mounted under `/api/v1/public/portal/ai-extract*`, and not
  `product_specifications.preview_spec_search` (the branch's second fix), is a
  coroutine endpoint.
* AC-4 - upload bytes reach the service intact via the sync `f.file.read()`
  path (checked inside the AC-1 test).

Same shape as PR #164's AC-J2/AC-J3 dealer-kit tests.

Postgres only (`tests/_pg_fixture.blank_session`), rows seeded by the test
with a `ZZT-` marker prefix.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session
from tests._portal_grant import link_contact_company

EXTRACT_URL = "/api/v1/public/portal/ai-extract"

# A hardcoded, valid 1x1 PNG (not built with an image library so this file has
# no new dependency).
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _files(count: int = 1):
    return [
        ("files", (f"zzt-tiny-{i + 1}.png", _TINY_PNG, "image/png"))
        for i in range(count)
    ]


@pytest.fixture
def api():
    """A portal-token-authenticated client on a blank Postgres schema."""
    from app.database import get_db

    with blank_session() as db:

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield db
        finally:
            app.dependency_overrides.clear()


def _contact(db):
    from app.models.access import RespondContact

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+6011{uuid.uuid4().hex[:8]}",
        name="ZZT-off-loop-contact",
    )
    db.add(contact)
    db.flush()
    link_contact_company(db, contact.id)
    return contact


def _token(db, contact):
    from app.models.portal import PortalToken

    token = PortalToken(
        id=str(uuid.uuid4()),
        token=f"ZZT-tok-{uuid.uuid4().hex}",
        contact_id=contact.id,
        space_id="ZZT-off-loop-space",
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(token)
    db.commit()
    return token


def _minimal_result():
    from app.services.ai_extract.extract_service import ExtractResult

    return ExtractResult(values={})


# --------------------------------------------------------------------------- #
# AC-1 / AC-4: the extraction runs off the event loop, and the sync read path
# hands the service intact bytes
# --------------------------------------------------------------------------- #
class TestOffTheLoop:
    def test_ac_1_extract_is_called_off_the_event_loop(self, api, monkeypatch) -> None:
        """The regression this whole slice exists to prevent.

        Patches the ROUTE module's ``AIExtractService.extract`` (the class
        attribute the route calls through, not a fresh import) so the spy
        intercepts the exact call the route makes, and asserts
        ``asyncio.get_running_loop()`` RAISES inside it - i.e. it is executing
        in a worker thread, not on the loop the TestClient's request is served
        from. A wall-clock threshold would be flaky on CI hardware; the SHAPE
        of "off the loop or not" never is.

        AC-4 rides along here: two files are uploaded and the spy records what
        it actually received. This pins the ``await f.read()`` ->
        ``f.file.read()`` swap - each multipart part must be read intact and
        independently, not with a stale/shared seek position. The spy never
        asserts itself (a failure there would surface as a bare 500, not a
        readable test failure); it only records into ``observed``, and every
        assertion runs in the main thread after the request completes.
        """
        from app.api.v1.public import ai_extract as route_module

        db = api
        contact = _contact(db)
        token = _token(db, contact)

        observed: dict[str, object] = {}

        def _spy(self, form_key, files, *, user_id=None, portal_contact_id=None):
            try:
                asyncio.get_running_loop()
                observed["on_loop"] = True
            except RuntimeError:
                observed["on_loop"] = False
            observed["filenames"] = [f.filename for f in files]
            observed["data"] = [f.data for f in files]
            return _minimal_result()

        monkeypatch.setattr(route_module.AIExtractService, "extract", _spy)

        with TestClient(app) as c:
            res = c.post(
                EXTRACT_URL,
                data={"form_key": "portal.complaint"},
                files=_files(2),
                headers={"X-Portal-Token": token.token},
            )

        assert res.status_code == 200, res.text
        assert "on_loop" in observed, "the spy was never called"
        assert observed["on_loop"] is False, (
            "AIExtractService.extract ran ON the event loop - this is the "
            "exact defect that froze a whole gunicorn worker for the "
            "duration of a PDF render plus LLM round trip."
        )

        # AC-4: both parts arrived, in order, with the actual bytes intact.
        assert observed["filenames"] == ["zzt-tiny-1.png", "zzt-tiny-2.png"], (
            f"unexpected filenames reached the service: {observed['filenames']}"
        )
        assert observed["data"] == [_TINY_PNG, _TINY_PNG], (
            "upload bytes did not reach the service intact via the sync "
            "f.file.read() path - each part must be read independently"
        )


# --------------------------------------------------------------------------- #
# AC-2: the loop stays responsive while an extract is in flight, by ORDERING
# --------------------------------------------------------------------------- #
class TestLoopStaysResponsive:
    def test_ac_2_a_cheap_request_completes_while_an_extract_is_in_flight(
        self, api, monkeypatch
    ) -> None:
        """Fires the extract request from a background thread, blocks the stub
        on a ``threading.Event`` so it cannot finish on its own, and asserts a
        second cheap request completes BEFORE the extract is released. No
        elapsed-time assertion: the proof is that the cheap request could be
        served AT ALL while the extract handler was still parked, which is
        only possible off the event loop.

        If the route regresses to running on the loop, the cheap request
        deadlocks behind it. The helper thread + ``join(timeout=...)`` below
        exists so that regression fails loudly instead of hanging CI.
        """
        from app.api.v1.public import ai_extract as route_module

        db = api
        contact = _contact(db)
        token = _token(db, contact)

        entered = threading.Event()
        release = threading.Event()

        def _stub(self, form_key, files, *, user_id=None, portal_contact_id=None):
            entered.set()
            # Generous safety-net timeout only - not what the test asserts on.
            release.wait(timeout=30)
            return _minimal_result()

        monkeypatch.setattr(route_module.AIExtractService, "extract", _stub)

        with TestClient(app) as c:
            extract_outcome: dict[str, object] = {}

            def _run_extract():
                try:
                    extract_outcome["response"] = c.post(
                        EXTRACT_URL,
                        data={"form_key": "portal.complaint"},
                        files=_files(1),
                        headers={"X-Portal-Token": token.token},
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced in the main thread below
                    extract_outcome["error"] = exc

            extract_thread = threading.Thread(target=_run_extract)
            extract_thread.start()

            assert entered.wait(timeout=10), "the stub was never entered"

            cheap_outcome: dict[str, object] = {}

            def _run_cheap():
                cheap_outcome["response"] = c.get("/health")

            cheap_thread = threading.Thread(target=_run_cheap)
            cheap_thread.start()
            cheap_thread.join(timeout=10)

            if cheap_thread.is_alive():
                # Unblock the extract so the client/thread can be torn down
                # cleanly, then fail loudly rather than hanging the suite.
                release.set()
                extract_thread.join(timeout=35)
                pytest.fail(
                    "a cheap /health request did not complete while an "
                    "extract was in flight - the extract is running on the "
                    "event loop and is blocking every other request on it"
                )

            # The cheap thread's join above only returns once its request has
            # completed, and that join happened before the line below runs -
            # THAT control flow is the proof of ordering. This assert is a
            # defensive guard only, not itself the proof.
            assert not release.is_set(), (
                "release was already set before the cheap request's join "
                "returned - the ordering guarantee above does not hold"
            )
            cheap_response = cheap_outcome["response"]
            assert cheap_response.status_code == 200, cheap_response.text

            release.set()
            extract_thread.join(timeout=35)
            assert not extract_thread.is_alive(), "the extract thread never finished"

        if "error" in extract_outcome:
            raise extract_outcome["error"]  # real traceback, not KeyError: 'response'
        extract_response = extract_outcome["response"]
        assert extract_response.status_code == 200, extract_response.text


# --------------------------------------------------------------------------- #
# AC-3: sweep for async def handlers doing heavy sync work
# --------------------------------------------------------------------------- #
def test_ac_3_ai_extract_and_spec_preview_search_are_not_coroutine_endpoints() -> None:
    """No route mounted under ``/api/v1/public/portal/ai-extract*`` does heavy
    synchronous work on the event loop, and neither does
    ``product_specifications.preview_spec_search`` - the branch's second fix,
    which had no route-level guard of its own before this test. Proven from
    FastAPI's own routing metadata: the registered endpoint must not be a
    coroutine function, which is exactly the property FastAPI keys threadpool
    dispatch on. Same shape as PR #164's AC-J3 sweep.
    """
    from fastapi.routing import APIRoute
    from app.api.v1.master_data import product_specifications

    def _must_stay_off_the_loop(route: APIRoute) -> bool:
        return route.path.startswith("/api/v1/public/portal/ai-extract") or (
            route.endpoint is product_specifications.preview_spec_search
        )

    guarded_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and _must_stay_off_the_loop(route)
    ]
    assert len(guarded_routes) >= 3, (
        "expected the two ai-extract routes plus preview-search to be mounted, "
        f"found {len(guarded_routes)}"
    )

    offenders: list[str] = []
    for route in guarded_routes:
        if inspect.iscoroutinefunction(route.endpoint):
            methods = ",".join(sorted(route.methods or []))
            offenders.append(f"{methods} {route.path} -> {route.endpoint.__qualname__}")

    assert offenders == [], (
        "coroutine endpoints found doing heavy synchronous work; FastAPI runs "
        "these ON the event loop instead of threadpooling them (make them "
        f"plain def): {offenders}"
    )
