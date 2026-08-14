from .adapters import (
    AdapterCancellation,
    AdapterError,
    AdapterExecution,
    AdapterRegistry,
    CancellationDisposition,
)
from .catalog import Catalog
from .models import TERMINAL_JOB_STATES, JobError, JobState, JobView, RetryDisposition, Subject
from .store import Store


class CancellationError(RuntimeError):
    pass


class Canceller:
    def __init__(self, store: Store, catalog: Catalog, adapters: AdapterRegistry) -> None:
        self.store = store
        self.catalog = catalog
        self.adapters = adapters

    async def cancel(self, job_id: str, subject: Subject) -> JobView:
        job = self.store.get_job(job_id)
        if job is None:
            raise CancellationError("job not found")
        request, owner = self.store.get_job_context(job_id)
        if (owner.name, owner.device) != (subject.name, subject.device):
            raise CancellationError("job belongs to another subject or device")
        if job.state in TERMINAL_JOB_STATES:
            raise CancellationError(f"job is already {job.state.value}")
        if job.state == JobState.QUEUED:
            return self.store.transition_job(
                job.id, JobState.CANCELLED, result=job.result, error=job.error
            )

        action = self.catalog.validate_request(request)
        adapter = self.adapters.get(action.adapter)
        execution = self._execution(job)
        try:
            result = await adapter.cancel(action, request, execution)
        except AdapterError as error:
            if error.reconciliation_required and job.state != JobState.UNKNOWN_OUTCOME:
                return self.store.transition_job(
                    job.id,
                    JobState.UNKNOWN_OUTCOME,
                    result=job.result,
                    error=error.as_job_error(),
                )
            raise CancellationError(str(error)) from error
        return self._apply(job, result)

    @staticmethod
    def _execution(job: JobView) -> AdapterExecution | None:
        raw = (job.result or {}).get("execution")
        return AdapterExecution.model_validate(raw) if isinstance(raw, dict) else None

    def _apply(self, job: JobView, result: AdapterCancellation) -> JobView:
        if result.disposition == CancellationDisposition.CANCELLED:
            return self.store.transition_job(
                job.id,
                JobState.CANCELLED,
                result={**(job.result or {}), "cancellation": result.model_dump(mode="json")},
            )
        if result.disposition == CancellationDisposition.UNKNOWN:
            if job.state == JobState.UNKNOWN_OUTCOME:
                return job
            return self.store.transition_job(
                job.id,
                JobState.UNKNOWN_OUTCOME,
                result=job.result,
                error=JobError(
                    code="cancellation_unknown",
                    message="adapter could not determine the cancellation outcome",
                    retry=RetryDisposition.RECONCILE_FIRST,
                    reconciliation_required=True,
                    details=result.details,
                ),
            )
        raise CancellationError(f"adapter reported cancellation {result.disposition.value}")
