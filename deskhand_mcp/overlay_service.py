"""Persistent local overlay service shared by independent MCP server processes."""

from __future__ import annotations

import json
import logging
import os
import threading
import time

logger = logging.getLogger("deskhand.overlay.service")

import win32file
import win32pipe

from .engine.overlay import AgentOverlay

PIPE_NAME = r"\\.\pipe\deskhand_agent_overlay"
IDLE_SECONDS = float(os.environ.get("DESKHAND_OVERLAY_IDLE_SECONDS", "0"))


class OverlayService:
    def __init__(self) -> None:
        self.controller = AgentOverlay()
        self.last_activity = time.monotonic()
        self.active = False
        self.user_paused = False
        self.stopping = False
        self.captures = []
        self.pipe = win32pipe.CreateNamedPipe(
            PIPE_NAME, win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, None,
        )

    def _begin(self, message: dict) -> dict:
        if self.user_paused:
            return {"ok": False, "paused": True, "error": "用户已按 Esc 暂停"}
        self.active = True
        self.last_activity = time.monotonic()
        self.controller.resume()
        event_id = self.controller.begin(message["tool"], message.get("args") or {})
        return {"ok": True, "event_id": event_id}

    def _status(self) -> dict:
        status = self.controller.status()
        status.update({
            "service_active": self.active,
            "idle_seconds": round(max(0.0, time.monotonic() - self.last_activity), 2),
        })
        return status

    def _handle(self, message: dict) -> dict:
        action = message.get("action")
        self.last_activity = time.monotonic()
        if action == "begin":
            return self._begin(message)
        if action == "end":
            self.controller.end(message["event_id"], bool(message.get("ok")),
                                float(message.get("duration") or 0.0))
            return {"ok": True}
        if action == "pause":
            self.active = False
            self.user_paused = True
            self.controller.pause()
            return {"ok": True, "paused": True}
        if action == "resume":
            self.active = True
            self.user_paused = False
            self.controller.resume()
            return {"ok": True, "resumed": True, **self._status()}
        if action == "status":
            return self._status()
        if action == "capture_start":
            self.captures.append(self.controller.capture_guard())
            self.captures[-1].__enter__()
            return {"ok": True}
        if action == "capture_end":
            if self.captures:
                self.captures.pop().__exit__(None, None, None)
            return {"ok": True}
        if action == "shutdown":
            self.stopping = True
            return {"ok": True}
        return {"ok": False, "error": f"未知 action: {action}"}

    def _idle_worker(self) -> None:
        while not self.stopping:
            if (IDLE_SECONDS > 0 and self.active and not self.user_paused
                    and time.monotonic() - self.last_activity > IDLE_SECONDS):
                self.active = False
                self.controller.pause()
            time.sleep(0.5)

    def _reset_client(self) -> None:
        try:
            win32pipe.DisconnectNamedPipe(self.pipe)
        except Exception:
            pass
        try:
            win32pipe.ConnectNamedPipe(self.pipe)
        except Exception as exc:
            logger.debug("覆盖层服务重置连接失败: %s", exc)
            time.sleep(0.05)

    def run(self) -> None:
        threading.Thread(target=self._idle_worker, daemon=True).start()
        while not self.stopping:
            try:
                _, data = win32file.ReadFile(self.pipe, 65536)
                message = json.loads(data.decode("utf-8"))
                response = self._handle(message)
            except Exception as exc:
                if self.stopping:
                    break
                logger.warning("覆盖层服务读取失败: %s", exc)
                response = {"ok": False, "error": str(exc)}
            try:
                win32file.WriteFile(self.pipe, json.dumps(response, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:
                logger.warning("覆盖层服务写入失败: %s", exc)
            finally:
                self._reset_client()
        self.controller.close()
        try:
            win32file.CloseHandle(self.pipe)
        except Exception:
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    OverlayService().run()


if __name__ == "__main__":
    main()
