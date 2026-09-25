from importlib.metadata import version

from fastapi import FastAPI

from app import __version__


def test_module_level_app_is_importable() -> None:
    from app.main import app

    assert isinstance(app, FastAPI)


def test_health_returns_ok(client, settings) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["name"] == settings.name
    assert body["environment"] == "test"
    assert body["version"] == __version__
    assert body["agent_runtime"]["framework"] == "agno"
    assert body["agent_runtime"]["version"] == version("agno")


def test_no_business_routes_registered(client) -> None:
    paths = {route.path for route in client.app.routes}

    assert paths - {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"} == {"/health"}
