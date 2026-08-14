import hmac
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .config import MacSettings, load_inventory
from .executor import LocalActionError, MacExecutor
from .models import LocalActionRequest, LocalActionResult


class Executor(Protocol):
    async def execute(self, request: LocalActionRequest) -> LocalActionResult: ...


def create_app(settings: MacSettings, executor: Executor | None = None) -> FastAPI:
    runtime_executor = executor or MacExecutor(load_inventory(settings.inventory_path))
    app = FastAPI(title="Deckhand macOS Agent", version="0.1.0")

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        try:
            expected = settings.token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "local credential unavailable"
            ) from error
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "local authorization required")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/actions:execute", response_model=LocalActionResult)
    async def execute(
        request: LocalActionRequest,
        _: Annotated[None, Depends(authenticate)],
    ) -> LocalActionResult:
        try:
            return await runtime_executor.execute(request)
        except LocalActionError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    return app
