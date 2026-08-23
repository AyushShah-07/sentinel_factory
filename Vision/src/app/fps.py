import collections
import time


class FpsMeter:
    def __init__(self, window=30):
        self._durations = collections.deque(maxlen=max(1, window))  # Store recent frame times
        self._last_time = None  # Time of last frame

    def tick(self):
        now = time.perf_counter()  # Get current time
        if self._last_time is not None:
            self._durations.append(now - self._last_time)  # Save time between frames
        self._last_time = now  # Update last frame time

    @property
    def fps(self):
        if not self._durations:
            return 0.0  # No frames yet
        avg = sum(self._durations) / len(self._durations)  # Average frame time
        return 1.0 / avg if avg > 0 else 0.0  # Convert to FPS