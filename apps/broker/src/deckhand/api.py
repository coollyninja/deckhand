import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.header import decode_header
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from prometheus_client import make_asgi_app

from .adapters import AdapterRegistry
from .catalog import Catalog, CatalogError
from .config import Settings
from .digests import request_digest
from .extensions import load_catalog, load_extensions
from .metrics import JOBS_SUBMITTED, POLICY_DECISIONS, STATUS_OBSERVATION_SECONDS
from .models import ActionDefinition, ActionRequest, JobView, PlanView, StatusValue, Subject
from .plugin_api import PluginManifest
from .policy import OpaPolicyEngine, PolicyEngine, PolicyUnavailable
from .status import StatusAggregator
from .store import Store, StoreError
from .worker import Worker


@dataclass
class Runtime:
    settings: Settings
    catalog: Catalog
    store: Store
    policy: PolicyEngine
    adapters: AdapterRegistry
    worker: Worker
    status: StatusAggregator


def create_app(
    settings: Settings | None = None,
    *,
    policy: PolicyEngine | None = None,
    adapters: AdapterRegistry | None = None,
) -> FastAPI:
    configured = settings or Settings()
    extensions = load_extensions(configured)
    catalog = load_catalog(configured, extensions)
    store = Store(configured.database_path)
    policy_engine = policy or OpaPolicyEngine(configured.opa_url, configured.opa_decision_path)
    adapter_registry = adapters or extensions.adapters
    runtime = Runtime(
        settings=configured,
        catalog=catalog,
        store=store,
        policy=policy_engine,
        adapters=adapter_registry,
        worker=Worker(configured.worker_id, store, catalog, adapter_registry),
        status=extensions.status,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime.store.initialize()
        yield

    app = FastAPI(title="Deckhand Broker", version="0.2.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.mount("/metrics", make_asgi_app())

    def authenticated_subject(
        tailscale_user_login: Annotated[str | None, Header()] = None,
        tailscale_app_capabilities: Annotated[str | None, Header()] = None,
        x_deckhand_subject: Annotated[str | None, Header()] = None,
        x_deckhand_device: Annotated[str | None, Header()] = None,
        x_deckhand_channel: Annotated[str | None, Header()] = None,
        x_deckhand_proxy_assertion: Annotated[str | None, Header()] = None,
    ) -> Subject:
        assertion_file = runtime.settings.proxy_assertion_file
        if not runtime.settings.trusted_proxy or assertion_file is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "trusted ingress unavailable")
        try:
            expected_assertion = assertion_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "proxy assertion unavailable"
            ) from error
        if not x_deckhand_proxy_assertion or not hmac.compare_digest(
            x_deckhand_proxy_assertion, expected_assertion
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "trusted proxy assertion required")

        if x_deckhand_channel == "tailscale":
            if not tailscale_user_login or not tailscale_app_capabilities:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tailscale identity required")
            decoded_parts = decode_header(tailscale_app_capabilities)
            decoded = "".join(
                part.decode(charset or "utf-8") if isinstance(part, bytes) else part
                for part, charset in decoded_parts
            )
            try:
                capabilities = json.loads(decoded)
                grants = capabilities[runtime.settings.tailscale_app_capability]
                device = grants[0]["device"]
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Deckhand app capability required"
                ) from error
            if not isinstance(device, str) or not device:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device identity required")
            return Subject(name=tailscale_user_login, device=device, channel="tailscale")

        if x_deckhand_channel != "mgmt-mtls" or not x_deckhand_subject or not x_deckhand_device:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authenticated identity required")
        return Subject(name=x_deckhand_subject, device=x_deckhand_device, channel="mgmt-mtls")

    def policy_input(
        action: ActionDefinition,
        request: ActionRequest,
        subject: Subject,
        *,
        phase: str,
        confirmation_valid: bool,
    ) -> dict[str, Any]:
        return {
            "action": action.model_dump(mode="json"),
            "subject": subject.model_dump(mode="json"),
            "target": {**request.target.model_dump(mode="json"), "protected": False},
            "parameters": request.parameters,
            "runtime": {
                "mutations_enabled": runtime.settings.allow_mutations,
                "audit_writable": runtime.store.audit_is_writable(),
            },
            "request": {"digest": request_digest(request), "phase": phase},
            "confirmation": {
                "valid": confirmation_valid,
                "request_digest": request_digest(request) if confirmation_valid else None,
            },
        }

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict[str, str]:
        failures: list[str] = []
        if not runtime.store.audit_is_writable():
            failures.append("audit")
        if not await runtime.policy.ready():
            failures.append("policy")
        if failures:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"dependencies unavailable: {', '.join(failures)}",
            )
        return {"status": "ready"}

    @app.get("/v1/actions")
    async def actions(
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> list[dict[str, object]]:
        return runtime.catalog.serializable()

    @app.get("/v1/plugins", response_model=list[PluginManifest])
    async def plugins(
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> tuple[PluginManifest, ...]:
        return extensions.manifests

    @app.get("/v1/status/summary")
    async def status_summary(
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> dict[str, StatusValue]:
        with STATUS_OBSERVATION_SECONDS.labels(scope="summary").time():
            return await runtime.status.summary()

    @app.get("/v1/status/{domain}", response_model=StatusValue)
    async def status_domain(
        domain: str,
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> StatusValue:
        with STATUS_OBSERVATION_SECONDS.labels(scope="domain").time():
            return await runtime.status.domain(domain)

    @app.post("/v1/actions/{action_id}:plan", response_model=PlanView)
    async def plan(
        action_id: str,
        request: ActionRequest,
        subject: Annotated[Subject, Depends(authenticated_subject)],
    ) -> PlanView:
        if action_id != request.action_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "action ID mismatch")
        try:
            action = runtime.catalog.validate_request(request)
            adapter_plan = await runtime.adapters.get(action.adapter).plan(action, request)
            decision = await runtime.policy.decide(
                policy_input(
                    action,
                    request,
                    subject,
                    phase="plan",
                    confirmation_valid=False,
                )
            )
            POLICY_DECISIONS.labels(
                phase="plan", outcome="allow" if decision.allow else "deny"
            ).inc()
        except CatalogError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except PolicyUnavailable as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

        challenge = None
        if decision.allow and action.mutation and decision.required_confirmation != "none":
            challenge = runtime.store.create_confirmation(
                request,
                subject,
                decision.required_confirmation,
                f"Confirm {action.title} for {request.target.type}:{request.target.id}",
                runtime.settings.confirmation_ttl_seconds,
            )
        return PlanView(
            request_digest=request_digest(request),
            action_id=action.id,
            action_version=action.version,
            target=request.target,
            mutation=action.mutation,
            executable=decision.allow,
            steps=adapter_plan.steps,
            required_confirmation=decision.required_confirmation,
            confirmation=challenge,
            denial_reason=None if decision.allow else decision.reason,
        )

    @app.post("/v1/actions/{action_id}:execute", response_model=JobView)
    async def execute(
        action_id: str,
        request: ActionRequest,
        subject: Annotated[Subject, Depends(authenticated_subject)],
    ) -> JobView:
        if action_id != request.action_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "action ID mismatch")
        try:
            action = runtime.catalog.validate_request(request)
            confirmation_valid = False
            if request.confirmation_token:
                confirmation_valid = runtime.store.consume_confirmation(
                    request, subject, request.confirmation_token
                )
            decision = await runtime.policy.decide(
                policy_input(
                    action,
                    request,
                    subject,
                    phase="execute",
                    confirmation_valid=confirmation_valid,
                )
            )
            POLICY_DECISIONS.labels(
                phase="execute", outcome="allow" if decision.allow else "deny"
            ).inc()
            if not decision.allow:
                raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)
            if request.dry_run:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "execute rejects dry-run requests",
                )
            job = runtime.store.create_job(request, subject)
            JOBS_SUBMITTED.labels(action_id=action.id).inc()
            return job
        except CatalogError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except PolicyUnavailable as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        except StoreError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    @app.get("/v1/jobs/{job_id}", response_model=JobView)
    async def job(job_id: str, _: Annotated[Subject, Depends(authenticated_subject)]) -> JobView:
        found = runtime.store.get_job(job_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return found

    @app.get("/v1/events")
    async def events(
        _: Annotated[Subject, Depends(authenticated_subject)],
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict[str, Any]]:
        return runtime.store.list_audit_events(after, limit)

    return app
