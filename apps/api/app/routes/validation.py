"""``SafeValidationRoute``: Product request-validation answers that never echo input.

Shared by every Product router. A 422 lists only the structural ``type``, ``loc`` and
``msg`` of each validation error: the submitted ``input`` and the validation ``ctx`` are
dropped, so no submitted value (message, title, description, malformed query value,
credential) is ever returned. FastAPI-only on purpose: importing it loads no Product
domain, execution or integration code (the Operations routes keep their isolation).
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class SafeValidationRoute(APIRoute):
    """422 answers that never contain submitted values (``input``/``ctx`` dropped)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                detail = [
                    {
                        "type": e.get("type"),
                        "loc": [str(p) for p in e.get("loc", ())],
                        "msg": e.get("msg"),
                    }
                    for e in error.errors()
                ]
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail}
                )

        return safe_handler
