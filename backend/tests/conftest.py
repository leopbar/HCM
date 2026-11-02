import hashlib
import os
import pytest

# The suite runs against SQLite with a throwaway signing key; production refuses weak secrets.
os.environ.setdefault("HCM_ALLOW_INSECURE_SECRET", "1")
os.environ.setdefault("LOGIN_ATTEMPTS_PER_IP", "100000")  # every test logs in from the same client address


@pytest.fixture
def integration(client, monkeypatch):
    """Read credential of the ERP integration; `client` comes from test_api."""
    monkeypatch.setenv("HCM_INTEGRATION_TOKEN_SHA256", hashlib.sha256(b"test-only-integration-secret").hexdigest())
    http, refs = client
    return http, refs, {"Authorization": "Bearer test-only-integration-secret"}


@pytest.fixture
def executor(integration, monkeypatch):
    """Execution credential on top of the read credential."""
    http, refs, headers = integration
    monkeypatch.setenv("HCM_EXECUTION_TOKEN_SHA256", hashlib.sha256(b"execution-test-secret").hexdigest())
    return http, refs, {**headers, "X-HCM-Execution-Key": "execution-test-secret"}, headers
