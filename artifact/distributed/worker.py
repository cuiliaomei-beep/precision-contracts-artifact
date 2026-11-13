"""Repaired worker ownership/worklists; no placeholder incoming joins."""
from .event_solver import Worker

class WorkerMessage:
    def __init__(self, src_worker, dst_worker, delta, event_ids):
        self.src, self.dst = src_worker, dst_worker
        self.delta, self.event_ids = delta, list(event_ids)

__all__ = ["Worker", "WorkerMessage"]
