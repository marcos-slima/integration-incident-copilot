"""DA-73: erro HTTP com `code` estavel para as UIs traduzirem.

`detail` continua em texto (compatibilidade com clientes e testes), mas a UI
nao depende dele: mostra o texto do catalogo do idioma do usuario para o
`code`, interpolando `params`. Codigo desconhecido pela UI -> `detail`.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class CodedHTTPException(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.params = params or {}


def api_error(
    status_code: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    **params: Any,
) -> CodedHTTPException:
    return CodedHTTPException(status_code, code, detail, params=params, headers=headers)


async def coded_http_exception_handler(request: Request, exc: CodedHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code, "params": exc.params},
        headers=exc.headers,
    )
