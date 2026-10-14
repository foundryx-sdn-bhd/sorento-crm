"""PR #1679 crew hand-test setup script: which databases and ingest URLs it accepts.

`scripts/handtest_1679_setup.py` writes to the database it is pointed at (through the copy's
ingest), so its guard is the only thing between it and a real database. Pure: no database.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "handtest_1679_setup.py"
_spec = importlib.util.spec_from_file_location("handtest_1679_setup", _PATH)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

INGEST = "http://localhost:8106"


def _db(name: str, host: str = "localhost") -> str:
    return f"postgresql://u:p@{host}:5432/{name}"


def test_the_crew_shared_dev_database_is_accepted_by_exact_name():
    assert script.guard(_db("sorento_cagent_stack"), INGEST) == "sorento_cagent_stack"


def test_allow_db_accepts_only_the_exact_name():
    assert script.guard(_db("sorento_stack2"), INGEST, allow_db="sorento_stack2") == "sorento_stack2"
    with pytest.raises(script.Refused):
        script.guard(_db("sorento_stack2"), INGEST, allow_db="sorento_stack")
    with pytest.raises(script.Refused):
        script.guard(_db("sorento_cagent_stack_prod"), INGEST)


def test_a_dev_looking_name_is_accepted():
    assert script.guard(_db("sorento_handtest_dev"), "http://127.0.0.1:8106") == "sorento_handtest_dev"


def test_any_other_name_is_refused():
    with pytest.raises(script.Refused, match="--allow-db sorento"):
        script.guard(_db("sorento"), INGEST)


@pytest.mark.parametrize("db,ingest,allow", [
    (_db("sorento_cagent_stack", host="10.0.0.5"), INGEST, None),
    (_db("sorento_cagent_stack", host="db.example.com"), INGEST, "sorento_cagent_stack"),
    (_db("sorento_cagent_stack"), "http://10.0.0.5:8106", None),
    (_db("sorento_cagent_stack"), "https://crm.example.com", "sorento_cagent_stack"),
])
def test_an_allowed_name_never_lifts_the_localhost_checks(db, ingest, allow):
    with pytest.raises(script.Refused, match="not this machine"):
        script.guard(db, ingest, allow)
