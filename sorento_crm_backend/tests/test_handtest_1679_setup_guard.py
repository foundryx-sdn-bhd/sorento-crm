"""PR #1679 crew hand-test setup script: which databases it accepts.

`scripts/handtest_1679_setup.py` writes cancel documents into the database it is connected
to, so its guard is the only thing between it and a real database: the URL as configured
(`guard`) and what Postgres reports once connected (`guard_connected`). Pure: no database.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "handtest_1679_setup.py"
_spec = importlib.util.spec_from_file_location("handtest_1679_setup", _PATH)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _db(name: str, host: str = "localhost", query: str = "") -> str:
    return f"postgresql://u:p@{host}:5432/{name}{query}"


@pytest.fixture(autouse=True)
def _no_pg_env(monkeypatch):
    for var in script._PG_ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)


def test_the_crew_shared_dev_database_is_accepted_by_exact_name():
    assert script.guard(_db("sorento_cagent_stack")) == "sorento_cagent_stack"
    assert script.guard_connected("sorento_cagent_stack", "127.0.0.1") == "sorento_cagent_stack"


def test_allow_db_accepts_only_the_exact_name():
    assert script.guard(_db("sorento_stack2"), allow_db="sorento_stack2") == "sorento_stack2"
    with pytest.raises(script.Refused):
        script.guard(_db("sorento_stack2"), allow_db="sorento_stack")
    with pytest.raises(script.Refused):
        script.guard(_db("sorento_cagent_stack_prod"))


@pytest.mark.parametrize("name", ["sorento_handtest_dev", "sorento_scm_ci", "test_copy",
                                  "dev"])
def test_a_dev_segment_is_accepted(name):
    assert script.guard(_db(name)) == name


@pytest.mark.parametrize("name", ["sorento", "sorento_crm", "sorento_latest", "prod_contest",
                                  "sorento_devops", "sorento_copyright"])
def test_any_other_name_is_refused(name):
    with pytest.raises(script.Refused, match=f"--allow-db {name}"):
        script.guard(_db(name))


@pytest.mark.parametrize("url", [
    _db("sorento_cagent_stack", host="10.0.0.5"),
    _db("sorento_cagent_stack", host="db.example.com"),
    _db("sorento_cagent_stack", host="localhost.evil.com"),
])
def test_a_database_host_off_this_machine_is_refused(url):
    with pytest.raises(script.Refused, match="not this machine"):
        script.guard(url, allow_db="sorento_cagent_stack")


@pytest.mark.parametrize("query", ["?host=prod.example.com", "?hostaddr=10.1.1.1",
                                   "?dbname=sorento", "?service=prod"])
def test_a_query_string_that_redirects_libpq_is_refused(query):
    with pytest.raises(script.Refused, match="query string"):
        script.guard(_db("sorento_cagent_stack", query=query))


def test_a_pg_variable_that_redirects_libpq_is_refused(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "10.1.1.1")
    with pytest.raises(script.Refused, match="PGHOSTADDR"):
        script.guard(_db("sorento_cagent_stack"))


@pytest.mark.parametrize("addr", [None, "127.0.0.1", "::1"])
def test_the_connected_server_on_this_machine_is_accepted(addr):
    assert script.guard_connected("sorento_cagent_stack", addr) == "sorento_cagent_stack"


@pytest.mark.parametrize("name,addr,match", [
    ("sorento_cagent_stack", "10.0.0.5", "not this machine"),
    ("sorento", "127.0.0.1", "not a dev"),
    (None, "127.0.0.1", "not a dev"),
])
def test_the_connected_server_elsewhere_or_named_otherwise_is_refused(name, addr, match):
    with pytest.raises(script.Refused, match=match):
        script.guard_connected(name, addr)
