import redis
import json

class Queue:
    def __init__(self, url, stream):
        self.r = redis.Redis.from_url(url)
        self.stream = stream

    def push(self, task):
        self.r.xadd(self.stream, {"data": json.dumps(task)})

    def read(self, last_id="$"):
        return self.r.xread({self.stream: last_id}, block=5000, count=1)