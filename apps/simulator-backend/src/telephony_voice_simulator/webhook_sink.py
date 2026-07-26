"""Local HTTP sink that captures the agent's webhooks for a simulated call.

The dispatch metadata points the agent's ``webhook_url`` at this server, so
every lifecycle event (``call.started`` ... ``call.ended``) for the simulated
call lands here. The runner awaits ``call.ended`` and hands the captured
payload to the assertion layer.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web


class WebhookSink:
    def __init__(self, host: str = "127.0.0.1", port: int = 8977) -> None:
        self.host = host
        self.port = port
        self.events: list[dict[str, Any]] = []
        self._new_event = asyncio.Condition()
        self._runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/webhook"

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/webhook", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {"_raw": await request.text()}
        async with self._new_event:
            self.events.append(payload)
            self._new_event.notify_all()
        return web.json_response({"ok": True})

    def events_for(self, call_id: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("call_id") == call_id]

    async def wait_for_event(
        self, event: str, call_id: str, timeout: float = 240.0
    ) -> dict[str, Any] | None:
        """Await a specific event for a call; None on timeout."""

        def _find() -> dict[str, Any] | None:
            for e in self.events:
                if e.get("event") == event and e.get("call_id") == call_id:
                    return e
            return None

        deadline = asyncio.get_running_loop().time() + timeout
        async with self._new_event:
            while True:
                found = _find()
                if found is not None:
                    return found
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._new_event.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return None

    def dump(self, path) -> None:
        path.write_text(json.dumps(self.events, indent=2))
