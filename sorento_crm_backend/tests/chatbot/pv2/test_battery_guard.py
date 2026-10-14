"""PV2-EVAL UAC-6: nothing leaves. The guard blocks every RQ enqueue and Respond send."""
from __future__ import annotations

import pytest

from scripts.pv2_battery import guard

GOOD_ENV = {"HTTPS_PROXY": "http://127.0.0.1:9", "DATABASE_URL": "postgresql://u:p@localhost:5432/sorento_cagent_stack"}


@pytest.fixture()
def installed():
    # Bound-name patching only means something if the importer is loaded before install.
    import app.services.stock_ask_service  # noqa: F401
    from app.services import queue_service, stock_ask_service

    original = queue_service.enqueue_job
    guard.install(dict(GOOD_ENV))
    try:
        yield
    finally:
        guard.uninstall()
        assert stock_ask_service.enqueue_job is original
        assert queue_service.enqueue_job is original


@pytest.fixture(autouse=True)
def _always_uninstall():
    yield
    guard.uninstall()


def test_exceptions_are_runtime_errors():
    assert issubclass(guard.OutboundBlocked, RuntimeError)
    assert issubclass(guard.GuardError, RuntimeError)


def test_check_env_ok():
    assert guard.check_env(dict(GOOD_ENV)) == []
    ok2 = dict(GOOD_ENV, DATABASE_URL="postgresql://u:p@127.0.0.1/sorento_cagent_stack")
    assert guard.check_env(ok2) == []


@pytest.mark.parametrize("over", [
    {"HTTPS_PROXY": "http://proxy.example:8080"},
    {"HTTPS_PROXY": ""},
    {"DATABASE_URL": "postgresql://u:p@db.prod.example.com/sorento"},
    {"DATABASE_URL": "postgresql://u:p@localhost/sorento_prod"},
])
def test_check_env_problems(over):
    assert guard.check_env(dict(GOOD_ENV, **over))


def test_check_env_missing_proxy():
    env = dict(GOOD_ENV)
    del env["HTTPS_PROXY"]
    assert guard.check_env(env)


def test_install_refuses_bad_env():
    with pytest.raises(guard.GuardError):
        guard.install(dict(GOOD_ENV, HTTPS_PROXY="http://example.com:3128"))
    with pytest.raises(guard.GuardError):
        guard.install(dict(GOOD_ENV, DATABASE_URL="postgresql://u:p@localhost/prod_copy"))


def test_rq_enqueue_call_blocked(installed):
    import rq

    q = rq.Queue.__new__(rq.Queue)
    with pytest.raises(guard.OutboundBlocked):
        rq.Queue.enqueue_call(q, print)


def test_enqueue_job_blocked_via_defining_module_and_bound_name(installed):
    from app.services import queue_service, stock_ask_service

    with pytest.raises(guard.OutboundBlocked):
        queue_service.enqueue_job(print, {}, queue_name="respond_io")
    with pytest.raises(guard.OutboundBlocked):
        stock_ask_service.enqueue_job(print, {}, queue_name="respond_io", job_timeout=180)


def test_notify_salesman_enqueue_path_blocked(installed):
    """The crew ask: a notify attempt after install() must raise, never reach a queue."""
    from app.services import stock_ask_service

    notify = getattr(stock_ask_service, "notify_salesman", None)
    assert notify is not None, "stock_ask_service.notify_salesman must exist"
    with pytest.raises(guard.OutboundBlocked):
        stock_ask_service.enqueue_job(notify, {"x": 1}, queue_name="respond_io", job_timeout=180)


def test_respond_send_blocked(installed):
    from app.services import respond_messaging_service as rms, stock_ask_service

    with pytest.raises(guard.OutboundBlocked):
        rms.send_text_or_template(None, "123456789", "hello")
    with pytest.raises(guard.OutboundBlocked):
        stock_ask_service.send_text_or_template(None, "123456789", "hello")


def test_uninstall_restores_everything():
    from app.services import queue_service, respond_messaging_service as rms, stock_ask_service
    import rq

    before = (rq.Queue.enqueue_call, queue_service.enqueue_job, stock_ask_service.enqueue_job,
              rms.send_text_or_template, stock_ask_service.send_text_or_template)
    guard.install(dict(GOOD_ENV))
    guard.uninstall()
    after = (rq.Queue.enqueue_call, queue_service.enqueue_job, stock_ask_service.enqueue_job,
             rms.send_text_or_template, stock_ask_service.send_text_or_template)
    assert before == after


def test_patch_envelope_strips_console_origin():
    env = {"is_test": True, "console_origin": {"user": "x"}, "run_id": "r1", "text": "hi"}
    out = guard.patch_envelope(env)
    assert "console_origin" not in out
    assert out["is_test"] is True and out["run_id"] == "r1" and out["text"] == "hi"


@pytest.mark.parametrize("env", [{"console_origin": {}}, {"is_test": False, "console_origin": {}}, {"is_test": "yes"}])
def test_patch_envelope_requires_is_test(env):
    with pytest.raises(guard.GuardError):
        guard.patch_envelope(env)


def test_force_parser_mode():
    from app.services.chatbot.pv2 import flag

    original = flag.parser_mode
    with guard.force_parser_mode("v1"):
        assert flag.parser_mode(None, None, {}) == "v1"
    with guard.force_parser_mode("v2"):
        assert flag.parser_mode(None, None, {}) == "v2"
    assert flag.parser_mode is original
