import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.header import decode_header
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from prometheus_client import make_asgi_app

from .adapters import AdapterError, AdapterHealth, AdapterRegistry
from .cancellation import CancellationError, Canceller
from .catalog import Catalog, CatalogError
from .config import Settings
from .digests import confirmation_digest, request_digest
from .extensions import load_catalog, load_extensions
from .identity import IdentityError, load_public_key, verify_token
from .metrics import JOBS_SUBMITTED, POLICY_DECISIONS, STATUS_OBSERVATION_SECONDS
from .models import ActionDefinition, ActionRequest, JobView, PlanView, StatusValue, Subject
from .plugin_api import PluginManifest
from .policy import OpaPolicyEngine, PolicyEngine, PolicyUnavailable
from .resilience import ResilienceSnapshot
from .status import StatusAggregator
from .store import Store, StoreError, load_audit_key
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
    canceller: Canceller


def create_app(
    settings: Settings | None = None,
    *,
    policy: PolicyEngine | None = None,
    adapters: AdapterRegistry | None = None,
) -> FastAPI:
    configured = settings or Settings()
    extensions = load_extensions(configured)
    catalog = load_catalog(configured, extensions)
    identity_public_key = (
        load_public_key(configured.identity_public_key_file)
        if configured.identity_public_key_file is not None
        else None
    )
    store = Store(configured.database_path, audit_hmac_key=load_audit_key(configured))
    policy_engine = policy or OpaPolicyEngine(configured.opa_url, configured.opa_decision_path)
    adapter_registry = adapters or extensions.adapters
    canceller = Canceller(store, catalog, adapter_registry)
    runtime = Runtime(
        settings=configured,
        catalog=catalog,
        store=store,
        policy=policy_engine,
        adapters=adapter_registry,
        worker=Worker(configured.worker_id, store, catalog, adapter_registry),
        status=extensions.status,
        canceller=canceller,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime.store.initialize()
        # Verify the audit chain on startup so tampering or truncation is detected
        # rather than silently trusted; a broken chain fails startup fast.
        if not runtime.store.verify_audit_chain():
            raise RuntimeError("audit chain verification failed at startup")
        yield

    app = FastAPI(title="Deckhand Broker", version="0.5.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.mount("/metrics", make_asgi_app())

    def _subject_from_identity_token(token: str) -> Subject:
        # Primary path: a trusted local issuer (Caddy/Tailscale Serve for the deck,
        # the MCP server for agents) minted an Ed25519-signed assertion. The channel
        # is inside the signed payload, so a client cannot self-declare it, and the
        # broker verifies a signature rather than comparing a shared secret.
        if identity_public_key is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "identity verification unavailable"
            )
        try:
            claims = verify_token(identity_public_key, token)
        except IdentityError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid identity token") from error
        return claims.to_subject()

    def _subject_from_legacy_headers(
        *,
        tailscale_user_login: str | None,
        tailscale_app_capabilities: str | None,
        x_deckhand_subject: str | None,
        x_deckhand_device: str | None,
        x_deckhand_channel: str | None,
        x_deckhand_proxy_assertion: str | None,
    ) -> Subject:
        # Backward-compatible fallback (opt-in): shared assertion + trusted headers.
        # Kept only for deployments not yet migrated to signed tokens.
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

    def authenticated_subject(
        x_deckhand_identity: Annotated[str | None, Header()] = None,
        tailscale_user_login: Annotated[str | None, Header()] = None,
        tailscale_app_capabilities: Annotated[str | None, Header()] = None,
        x_deckhand_subject: Annotated[str | None, Header()] = None,
        x_deckhand_device: Annotated[str | None, Header()] = None,
        x_deckhand_channel: Annotated[str | None, Header()] = None,
        x_deckhand_proxy_assertion: Annotated[str | None, Header()] = None,
    ) -> Subject:
        if x_deckhand_identity is not None:
            return _subject_from_identity_token(x_deckhand_identity)
        if identity_public_key is not None and not runtime.settings.allow_legacy_proxy_assertion:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signed identity token required")
        if not runtime.settings.allow_legacy_proxy_assertion:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "identity verification unavailable"
            )
        return _subject_from_legacy_headers(
            tailscale_user_login=tailscale_user_login,
            tailscale_app_capabilities=tailscale_app_capabilities,
            x_deckhand_subject=x_deckhand_subject,
            x_deckhand_device=x_deckhand_device,
            x_deckhand_channel=x_deckhand_channel,
            x_deckhand_proxy_assertion=x_deckhand_proxy_assertion,
        )

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

    @app.get("/v1/plugins/health", response_model=dict[str, AdapterHealth])
    async def plugin_health(
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> dict[str, AdapterHealth]:
        return await runtime.adapters.health()

    @app.get("/v1/plugins/resilience", response_model=dict[str, ResilienceSnapshot])
    async def plugin_resilience(
        _: Annotated[Subject, Depends(authenticated_subject)],
    ) -> dict[str, ResilienceSnapshot]:
        return {
            plugin_id: await guard.snapshot()
            for plugin_id, guard in sorted(extensions.resilience.items())
        }

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
            if not decision.allow:
                runtime.store.record_policy_denial(request, subject, "plan", decision.reason)
        except CatalogError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except AdapterError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {"code": error.kind.value, "retry": error.retry.value},
            ) from None
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
            confirmation_digest=confirmation_digest(request),
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
                    request, subject, request.confirmation_token, request.confirmation_response
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
                runtime.store.record_policy_denial(request, subject, "execute", decision.reason)
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

    @app.post("/v1/jobs/{job_id}:cancel", response_model=JobView)
    async def cancel_job(
        job_id: str, subject: Annotated[Subject, Depends(authenticated_subject)]
    ) -> JobView:
        try:
            return await runtime.canceller.cancel(job_id, subject)
        except CancellationError as error:
            code = (
                status.HTTP_404_NOT_FOUND
                if str(error) == "job not found"
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(code, str(error)) from error

    @app.post("/v1/confirmations/{confirmation_id}:cancel")
    async def cancel_confirmation(
        confirmation_id: str,
        subject: Annotated[Subject, Depends(authenticated_subject)],
    ) -> dict[str, str]:
        cancelled = runtime.store.cancel_confirmation(confirmation_id, subject)
        if not cancelled:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "confirmation not found or already resolved"
            )
        return {"status": "cancelled"}

    @app.get("/v1/events")
    async def events(
        _: Annotated[Subject, Depends(authenticated_subject)],
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict[str, Any]]:
        return runtime.store.list_audit_events(after, limit)

    return app
