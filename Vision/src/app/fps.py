import collections
import time



# ---------------------------------------------------------------------------
# FPS
# ---------------------------------------------------------------------------

class FpsMeter:
    """Rolling-average FPS, so the number on screen does not jitter."""

    def __init__(self, window=30):
        self._durations = collections.deque(
            maxlen=max(1, window)
        )
        self._last_time = None

    def tick(self):
        now = time.perf_counter()

        if self._last_time is not None:
            self._durations.append(
                now - self._last_time
            )

        self._last_time = now

    @property
    def fps(self):
        if not self._durations:
            return 0.0

        average = sum(self._durations) / len(self._durations)

        return (
            1.0 / average
            if average > 0
            else 0.0
        )
