"""Client for the persistent Deskhand overlay service."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager

import win32file

from .overlay_service import PIPE_NAME

logger = logging.getLogger("deskhand.overlay.client")


class OverlayClient:
    def _ensure_service(self) -> None:
        subprocess.Popen(
            [sys.executable, "-m", "deskhand_mcp.overlay_service"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                           | subprocess.CREATE_NO_WINDOW),
        )

    def _request(self, action: str, **payload) -> dict:
        message = json.dumps({"action": action, **payload}, ensure_ascii=False).encode("utf-8")
        last_error = None
        for attempt in range(40):
            try:
                handle = win32file.CreateFile(
                    PIPE_NAME, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None)
                try:
                    win32file.WriteFile(handle, message)
                    _, data = win32file.ReadFile(handle, 65536)
                    return json.loads(data.decode("utf-8"))
                finally:
                    win32file.CloseHandle(handle)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self._ensure_service()
                time.sleep(0.05 if attempt < 20 else 0.1)
        raise RuntimeError(f"覆盖层服务连接失败: {last_error}")

    def begin(self, tool: str, args: dict) -> int:
        try:
            result = self._request("begin", tool=tool, args=args)
            return int(result.get("event_id", -1))
        except Exception as exc:
            logger.warning("覆盖层启动事件失败: %s", exc)
            return -1

    def end(self, event_id: int, ok: bool, duration: float) -> None:
        if event_id < 0:
            return
        try:
            self._request("end", event_id=event_id, ok=ok, duration=duration)
        except Exception as exc:
            logger.debug("覆盖层结束事件失败: %s", exc)

    def pause(self) -> None:
        try:
            self._request("pause")
        except Exception as exc:
            logger.debug("覆盖层暂停失败: %s", exc)

    def resume(self) -> None:
        try:
            self._request("resume")
        except Exception as exc:
            logger.debug("覆盖层恢复失败: %s", exc)

    def status(self) -> dict:
        try:
            return self._request("status")
        except Exception as exc:
            return {"ok": False, "error": str(exc), "visible": False, "paused": False}

    def is_paused(self) -> bool:
        return bool(self.status().get("paused"))

    def should_cancel(self) -> bool:
        return self.is_paused()

    @contextmanager
    def capture_guard(self):
        status = self.status()
        if status.get("capture_excluded") or not status.get("visible"):
            yield
            return
        self._request("capture_start")
        try:
            yield
        finally:
            self._request("capture_end")

    def shutdown(self) -> dict:
        try:
            return self._request("shutdown")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close(self) -> None:
        return
