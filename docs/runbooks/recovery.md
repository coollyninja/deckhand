# Recovery runbook

## Broker unavailable

1. Confirm the physical deck shows broker actions offline; local Mac actions should remain available.
2. Reach the VM through the independent Proxmox console/admin endpoint. Do not depend on Deckhand to recover itself.
3. Check NTP, disk space, OPA, SQLite/audit writability, proxy assertion, and broker logs.
4. Keep mutation disabled until `/readyz` passes and `verify_audit_chain()` succeeds.

## Worker interrupted during mutation

1. Do not press the key again or manually replay the action.
2. Allow the lease to expire into `UNKNOWN_OUTCOME`.
3. The scheduler observes target state through the adapter and resolves success/failure. If observation remains unavailable, leave the job unknown and use the source system's UI/API.

## Tailnet unavailable

Use the management mTLS listener from an approved endpoint. If both paths fail, recover through Proxmox/PiKVM; do not expose a temporary public port or tunnel.

## Credential suspected compromised

Revoke only the affected purpose credential first, disable its action family in OPA, inspect audit and target logs, rotate the credential, prove old authority no longer works, then re-enable the smallest policy scope.

## Database restore

Stop broker, worker, and scheduler. Preserve the failed database for forensics. Restore an online-backup artifact to a new path, verify integrity and the audit chain, start read-only, reconcile all nonterminal jobs from source state, and obtain operator approval before mutation resumes.

