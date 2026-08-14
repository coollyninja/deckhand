from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .catalog import Catalog, CatalogError
from .config import Settings
from .models import ActionRequest, JobView, Subject
from .store import Store, StoreError


@dataclass
class Runtime:
    settings: Settings
    catalog: Catalog
    store: Store


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings()
    runtime = Runtime(
        settings=configured,
        catalog=Catalog.from_path(configured.catalog_path),
        store=Store(configured.database_path),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime.store.initialize()
        yield

    app = FastAPI(title="Deckhand Broker", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime

    def authenticated_subject(
        x_deckhand_subject: Annotated[str | None, Header()] = None,
        x_deckhand_device: Annotated[str | None, Header()] = None,
        x_deckhand_channel: Annotated[str | None, Header()] = None,
    ) -> Subject:
        if not runtime.settings.trusted_proxy:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "trusted ingress unavailable")
        if not x_deckhand_subject or not x_deckhand_device or not x_deckhand_channel:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authenticated identity required")
        return Subject(
            name=x_deckhand_subject, device=x_deckhand_device, channel=x_deckhand_channel
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict[str, str]:
        if not runtime.store.audit_is_writable():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "audit store unavailable")
        return {"status": "ready"}

    @app.get("/v1/actions")
    async def actions(
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> list[dict[str, object]]:
        return runtime.catalog.serializable()

    @app.post("/v1/actions/{action_id}:plan")
    async def plan(
        action_id: str,
        request: ActionRequest,
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> dict[str, object]:
        if action_id != request.action_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "action ID mismatch")
        try:
            action = runtime.catalog.validate_request(request)
        except CatalogError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        return {
            "action_id": action.id,
            "action_version": action.version,
            "target": request.target.model_dump(),
            "mutation": action.mutation,
            "confirmation": action.confirmation,
            "executable": not action.mutation or runtime.settings.allow_mutations,
        }

    @app.post("/v1/actions/{action_id}:execute", response_model=JobView)
    async def execute(
        action_id: str,
        request: ActionRequest,
        subject: Annotated[Subject, Depends(authenticated_subject)],
    ) -> JobView:
        if action_id != request.action_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "action ID mismatch")
        try:
            action = runtime.catalog.validate_request(request)
            if action.mutation and not runtime.settings.allow_mutations:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "mutations are disabled")
            if request.dry_run:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "execute rejects dry-run requests"
                )
            return runtime.store.create_job(request, subject)
        except CatalogError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        except StoreError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    @app.get("/v1/jobs/{job_id}", response_model=JobView)
    async def job(job_id: str, _: Annotated[Subject, Depends(authenticated_subject)]) -> JobView:
        found = runtime.store.get_job(job_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return found

    return app
