from __future__ import annotations

import os
import time

from deskhand_mcp.overlay_client import OverlayClient

os.environ["DESKHAND_OVERLAY_IDLE_SECONDS"] = "0"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message)


def main() -> None:
    first = OverlayClient()
    second = OverlayClient()
    try:
        event_id = first.begin("look", {"window": "Notepad"})
        check(event_id > 0, "覆盖层服务接受首个事件")
        time.sleep(0.4)
        first.end(event_id, True, 0.4)
        status = first.status()
        check(status.get("visible") and status.get("service_active"), "服务处于显示状态")
        first.close()
        status = second.status()
        check(status.get("visible") and status.get("service_active"),
              "客户端退出后覆盖层仍保持显示")
        time.sleep(1.5)
        check(second.status().get("visible"), "无新事件也不会自动隐藏")
        second.pause()
        check(second.status().get("paused"), "Esc 暂停状态跨进程可见")
        second.resume()
        check(not second.status().get("paused") and second.status().get("visible"),
              "恢复后继续显示")
        result = second.shutdown()
        check(result.get("ok") is True, "显式退出动作关闭服务")
    finally:
        second.shutdown()
    print("Overlay service checks completed")


if __name__ == "__main__":
    main()
