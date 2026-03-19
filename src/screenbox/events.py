"""Screenbox event bus -- pub/sub for real-time state changes.

Subscribers receive events via asyncio.Queue (for SSE streaming).
Emitters call emit() from any thread (sync or async).
"""

import asyncio
import json
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("screenbox.events")


class EventBus:
    """Thread-safe event bus with async subscriber queues."""

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        with self._lock:
            self._subscribers.add(queue)
        log.debug("Subscriber added (total=%d)", len(self._subscribers))
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Remove a subscriber queue."""
        with self._lock:
            self._subscribers.discard(queue)
        log.debug("Subscriber removed (total=%d)", len(self._subscribers))

    def emit(self, event: str, desktop_id: str = "",
             data: Optional[dict] = None):
        """Emit event to all subscribers. Thread-safe."""
        msg = {"event": event, "ts": time.time()}
        if desktop_id:
            msg["desktop_id"] = desktop_id
        if data:
            msg.update(data)

        dead: list[asyncio.Queue] = []
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)
                log.warning("Dropped slow subscriber (queue full)")

        log.debug("Event emitted: %s desktop=%s subs=%d",
                  event, desktop_id, len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Module-level singleton
event_bus = EventBus()
