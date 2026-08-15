# ADR-0003: Central per-plugin resilience guard

- Status: accepted
- Date: 2026-08-14

## Decision

Deckhand applies one shared resilience guard to every adapter and status provider contributed by
an enabled plugin. The plugin manager installs wrappers after validating the contribution, so the
plugin API remains unchanged and plugins cannot accidentally bypass the deployment's local
admission policy.

Each activation has bounded runtime settings for call timeout, maximum concurrency, token-bucket
rate, burst, transient-failure threshold, and circuit recovery interval. Defaults are conservative
and may be tightened in a private site overlay. The circuit is shared across a plugin's components
because they normally share an upstream dependency, credential, and egress destination.

Timeout covers local admission plus the plugin call. A timeout after a mutation begins produces
`UnknownOutcome`; a timeout before dispatch or during a read is safely retryable. Unexpected plugin
exceptions are converted to sanitized `AdapterError` values. Authentication, authorization,
configuration, not-found, and conflict failures do not open the availability circuit.

## Consequences

- One failing plugin cannot consume unbounded broker concurrency or request rate.
- Open circuits fail quickly, admit one half-open recovery probe, and never block unrelated plugins.
- Status summaries observe independent domains concurrently while each plugin's shared guard still
  enforces its own limits.
- Prometheus metrics expose bounded-label call outcomes, latency, queueing, in-flight work, and
  circuit state.
- Authenticated operators can inspect sanitized state at `GET /v1/plugins/resilience`.
- This protects the broker process from cooperative in-process plugins; it does not replace the
  process isolation required for untrusted or mutation-capable plugins.
