"""Download speed and time remaining, smoothed over a short window. Stdlib only."""

from __future__ import annotations

from collections import deque

WINDOW_SECONDS = 15.0
MIN_SPAN_SECONDS = 2.0  # too short a window gives a wild speed


class RateMeter:
    def __init__(self, window: float = WINDOW_SECONDS) -> None:
        self.window = window
        self.samples: deque[tuple[float, int]] = deque()

    def add(self, now: float, written: int) -> None:
        if self.samples and written < self.samples[-1][1]:
            self.samples.clear()  # the count went back (a file restarted): measure afresh
        self.samples.append((now, written))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()

    def speed(self) -> float | None:
        """Bytes per second over the window, None until there is enough to tell."""
        if len(self.samples) < 2:
            return None
        (t0, w0), (t1, w1) = self.samples[0], self.samples[-1]
        if t1 - t0 < MIN_SPAN_SECONDS:
            return None
        rate = (w1 - w0) / (t1 - t0)
        return rate if rate > 0 else None

    def eta(self, written: int, total: int) -> float | None:
        rate = self.speed()
        if rate is None or total <= written:
            return None
        return (total - written) / rate


def format_eta(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
