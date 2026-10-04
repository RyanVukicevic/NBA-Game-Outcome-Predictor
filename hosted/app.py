"""Authenticated Cloud Run HTTP entry point."""
from __future__ import annotations

import threading

from flask import Flask, jsonify

from hosted.worker import HostedWorker

app = Flask(__name__)
_lock = threading.Lock()


@app.get("/healthz")
def health():
    return jsonify(status="ok", service="courtside-worker")


@app.post("/run")
def run():
    if not _lock.acquire(blocking=False):
        return jsonify(status="busy"), 409
    try:
        return jsonify(HostedWorker().run())
    except RuntimeError as exc:
        return jsonify(status="failed", message=str(exc)), 500
    finally:
        _lock.release()
