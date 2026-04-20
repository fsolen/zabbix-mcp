import time

class TTLCache:
    def __init__(self, ttl):
        self.ttl = ttl
        self.store = {}

    def set(self, key, value):
        self.store[key] = (value, time.time())

    def get(self, key):
        v = self.store.get(key)
        if not v:
            return None
        val, ts = v
        if time.time() - ts > self.ttl:
            del self.store[key]
            return None
        return val