# Simple RPM + daily limiter with backoff, logs sleep time.
import time
from collections import deque

class RateLimiter:
    def __init__(self, rpm=10, burst=10, daily_limit=None):
        self.rpm = max(1, rpm)
        self.window = 60.0
        self.events = deque()
        self.burst = burst
        self.daily_limit = daily_limit
        self.daily_count = 0
        self.last_reset = time.time()

    def wait(self):
        now = time.time()

        # Reset daily window every 24h
        if now - self.last_reset >= 86400:
            self.daily_count = 0
            self.last_reset = now

        # Daily quota check
        if self.daily_limit and self.daily_count >= self.daily_limit:
            sleep_for = 86400 - (now - self.last_reset) + 1
            print(f"⏳ Daily limit {self.daily_limit} reached. Sleeping {sleep_for:.1f} sec…")
            time.sleep(sleep_for)
            self.daily_count = 0
            self.last_reset = time.time()

        # Drop old events outside the RPM window
        while self.events and now - self.events[0] > self.window:
            self.events.popleft()

        # If we’ve hit the RPM/burst limit, sleep until safe
        if len(self.events) >= self.burst or len(self.events) >= self.rpm:
            sleep_for = self.window - (now - self.events[0]) + 0.05
            if sleep_for > 0:
                print(f"⏳ RPM cap hit ({self.rpm}/min). Sleeping {sleep_for:.1f} sec…")
                time.sleep(min(sleep_for, 5.0))  # hard cap so we don’t oversleep
                self.events.append(time.time())
                self.daily_count += 1
                return sleep_for

        # Record event if no sleep needed
        self.events.append(time.time())
        self.daily_count += 1
        return 0.0
