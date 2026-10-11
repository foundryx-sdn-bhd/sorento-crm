"""Keep the chatbot eval runner tests DB-free.

The shared tests/conftest.py has a session-autouse fixture that sweeps scratch schemas in
Postgres. These tests are pure unit tests, so override it with a no-op for this directory.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _blank_schema_lifecycle():
    yield
