import pytest

from umsi_x_imetris_corporation_capstone.common import database

pytestmark = pytest.mark.unit


def test_connection_factory_uses_environment_and_returns_new_connections(monkeypatch):
    for key, value in {
        "POSTGRES_HOST": "test-db.internal",
        "POSTGRES_PORT": "6543",
        "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "synthetic_password",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(database.psycopg, "connect", connect)

    first = database.database_connection()
    second = database.database_connection()

    assert first is not second
    expected = {
        "dbname": "healthcare_edi",
        "host": "test-db.internal",
        "port": "6543",
        "user": "test_user",
        "password": "synthetic_password",
    }
    assert calls == [expected, expected]


def test_missing_database_configuration_fails_before_connecting(monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.setattr(
        database.psycopg,
        "connect",
        lambda **kwargs: pytest.fail("Unexpected connection"),
    )

    with pytest.raises(KeyError, match="POSTGRES_HOST"):
        database.database_connection()
