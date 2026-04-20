from fastapi import FastAPI
from cache import TTLCache
import time

app = FastAPI()
cache = TTLCache(600)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/analysis")
def analysis():
    return {"data": cache.store}