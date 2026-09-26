import pytest

PROTECTED_ENDPOINTS = ["/agents", f"/agents/{'runtime-smoke-test'}", "/sessions", "/config"]


@pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
def test_protected_endpoint_rejects_missing_credentials(client, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
def test_protected_endpoint_rejects_wrong_key(client, path: str) -> None:
    response = client.get(path, headers={"Authorization": "Bearer not-the-key"})

    assert response.status_code == 401


def test_valid_security_key_permits_access(client, auth_headers) -> None:
    response = client.get("/agents", headers=auth_headers)

    assert response.status_code == 200


def test_product_health_is_public(client) -> None:
    assert client.get("/health").status_code == 200
