"""Helpers to enumerate the effective routes of a FastAPI application."""

from fastapi.routing import APIRoute


def effective_api_routes(routes, prefix: str = "") -> list[tuple[str, str]]:
    """Return ``(method, path)`` for every API route, descending into included routers.

    FastAPI >= 0.141 keeps included routers as wrapper objects in ``app.routes``;
    this walks them so every route that can actually be served is listed.
    """
    found: list[tuple[str, str]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.extend((method, prefix + route.path) for method in route.methods or ())
        elif hasattr(route, "original_router"):
            child_prefix = prefix + getattr(route.include_context, "prefix", "")
            found.extend(effective_api_routes(route.original_router.routes, child_prefix))
    return found
