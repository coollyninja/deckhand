from .adapters import AdapterError, AdapterErrorKind, AdapterRegistry
from .catalog import Catalog, CatalogError
from .models import ActionRequest, JobState, JobView, RetryDisposition
from .store import StaleLease, Store

# Lease headroom over the action's own timeout so a lease does not lapse mid-call
# while the adapter is still legitimately working.
_LEASE_HEADROOM_SECONDS = 15
_DEFAULT_LEASE_SECONDS = 30


class Worker:
    def __init__(
        self, worker_id: str, store: Store, catalog: Catalog, adapters: AdapterRegistry
    ) -> None:
        self.worker_id = worker_id
        self.store = store
        self.catalog = catalog
        self.adapters = adapters

    def _lease_for(self, request: ActionRequest) -> int:
        try:
            action = self.catalog.validate_request(request)
        except CatalogError:
            return _DEFAULT_LEASE_SECONDS
        return action.timeout_seconds + _LEASE_HEADROOM_SECONDS

    async def run_once(self) -> JobView | None:
        claimed = self.store.claim_next_job(self.worker_id, lease_for=self._lease_for)
        if claimed is None:
            return None
        job = claimed.job
        fence = claimed.lease_token
        request, _ = self.store.get_job_context(job.id)
        action = self.catalog.validate_request(request)
        adapter = self.adapters.get(action.adapter)
        lease_seconds = self._lease_for(request)
        try:
            execution = await adapter.execute(action, request)
            partial_result = {"execution": execution.model_dump(mode="json")}
            self.store.transition_job(
                job.id, JobState.VERIFYING, result=partial_result, fence=fence
            )
            # Renew the lease before the observe/verify phase so a slow observation
            # does not let the lease lapse and get reclaimed under us.
            self.store.renew_lease(job.id, fence, lease_seconds)
            observation = await adapter.observe(action, request)
            verification = await adapter.verify(action, request, execution, observation)
            if not verification.satisfied:
                raise AdapterError(
                    "adapter postcondition is not yet satisfied",
                    kind=AdapterErrorKind.CONFLICT,
                    retry=RetryDisposition.RECONCILE_FIRST,
                    reconciliation_required=True,
                    details=verification.details,
                )
            return self.store.transition_job(
                job.id,
                JobState.SUCCEEDED,
                result={
                    **partial_result,
                    "observation": observation.model_dump(mode="json"),
                    "verification": verification.model_dump(mode="json"),
                },
                fence=fence,
            )
        except StaleLease:
            # Our lease was reclaimed (e.g. we stalled past expiry and the
            # reconciler took over). Abandon the job rather than fighting the new
            # owner; the reconciler now owns its resolution.
            return None
        except AdapterError as error:
            target = JobState.UNKNOWN_OUTCOME if error.reconciliation_required else JobState.FAILED
            try:
                return self.store.transition_job(
                    job.id, target, error=error.as_job_error(), fence=fence
                )
            except StaleLease:
                return None
