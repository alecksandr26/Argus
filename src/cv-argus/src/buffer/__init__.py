"""`buffer/` — the local SQLite alert queue. Saving and queuing ONLY (the "Queue Message Local
Buffer (SQLite)" box in the design diagram) — no communication logic of its own; `sender/`
dequeues from it. See `store.py`'s module docstring for the schema and concurrency design.
"""

from .store import Buffer, open_buffer

__all__ = ["Buffer", "open_buffer"]
