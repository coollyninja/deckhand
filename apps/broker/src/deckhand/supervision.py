"""Backoff for supervised daemon loops.

The worker and scheduler loops must never let a single failing iteration
crash-loop the process. They catch, log, and back off with jitter using this
helper so transient faults degrade to retry rather than taking the engine down.
"""

import random

_BASE_DELAY_SECONDS = 0.5
_MAX_DELAY_SECONDS = 30.0


def backoff_delay(consecutive_failures: int, *, jitter: bool = True) -> float:
    """Exponential backoff with full jitter, capped at _MAX_DELAY_SECONDS.

    consecutive_failures is 1-based (the first failure yields the base delay).
    """
    exponent = max(0, consecutive_failures - 1)
    ceiling: float = min(_BASE_DELAY_SECONDS * float(2**exponent), _MAX_DELAY_SECONDS)
    if not jitter or ceiling <= _BASE_DELAY_SECONDS:
        return ceiling
    # Non-cryptographic jitter for retry timing; unpredictability is not required.
    return float(random.uniform(_BASE_DELAY_SECONDS, ceiling))  # noqa: S311
