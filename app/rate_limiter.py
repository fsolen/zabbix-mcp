from ratelimit import limits, sleep_and_retry

class RateLimiter:
    def __init__(self, calls, period):
        self.calls = calls
        self.period = period

    def wrap(self, func):
        @sleep_and_retry
        @limits(calls=self.calls, period=self.period)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper