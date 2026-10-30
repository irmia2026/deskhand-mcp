from __future__ import annotations

import asyncio
import os
import signal
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from test_client import call, check, dismiss_save_dialog, find_el, PASS, FAIL

ROOT = os.path.dirname(os.path.abspath(__file__))


async def copied_text(session) -> str:
    data, _ = await call(session, "look", {"window": "", "image": False})
    editor = find_el(data.get("elements") or [], "文本编辑器", "Text Editor")
    selected, _ = await call(session, "read_selection", {
        "window": "", "element": editor["id"]})
    return selected.get("text") or ""


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "deskhand_mcp.server"], cwd=ROOT,
        env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            apps, _ = await call(session, "list_apps", {})
            check("StartApps 可枚举", apps.get("count", 0) > 0)
            launched, _ = await call(session, "launch_app", {
                "app": "notepad.exe", "wait_for_window": True, "timeout": 10})
            candidates = launched.get("candidate_windows") or []
            new_notepads = [w for w in candidates if "notepad" in w.get("title", "").lower()]
            window = launched.get("window") or (new_notepads[0] if len(new_notepads) == 1 else None)
            check("独立记事本窗口已识别", window is not None)
            if window is None:
                raise RuntimeError("无法确认独立记事本窗口，停止测试")
            hwnd = window["hwnd"]
            focused, _ = await call(session, "window_action", {
                "action": "focus", "hwnd": hwnd, "title": "Notepad"})
            check("hwnd 聚焦窗口", focused.get("ok") is True and focused.get("matched_via") == "hwnd")
            windows, _ = await call(session, "list_windows", {})
            check("list_windows 找到独立记事本", any(
                w.get("hwnd") == hwnd for w in windows.get("windows") or []))

            data, _ = await call(session, "look", {"hwnd": hwnd, "image": False})
            check("hwnd look", data.get("ok") is True)
            tab = find_el(data.get("elements") or [], "添加新标签页", "New tab")
            await call(session, "click", {"element": tab["id"]})
            await asyncio.sleep(0.5)
            data, _ = await call(session, "look", {"hwnd": hwnd, "image": False})
            editor = find_el(data.get("elements") or [], "文本编辑器", "Text Editor")
            await call(session, "append_text", {
                "text": "deskhand-mcp-e2e-capability-line\n第二行 capability",
                "window": "", "element": editor["id"]})

            data, _ = await call(session, "look", {"hwnd": hwnd, "image": False})
            bold = find_el(data.get("elements") or [], "加粗")
            text_anchor = next((e for e in data.get("elements") or []
                                if e.get("type") == "text" and "capability" in (e.get("name") or "")
                               and e.get("y", 0) > editor.get("top", 0)), None)
            first, _ = await call(session, "element_action", {"element": bold["id"], "action": "toggle"})
            check("显式 toggle 状态改变", first.get("ok") is True
                  and first.get("state_before") != first.get("state_after"),
                  str(first))
            second, _ = await call(session, "element_action", {"element": bold["id"], "action": "toggle"})
            check("toggle 可逆", second.get("ok") is True)

            check("三击文字锚点", text_anchor is not None)
            clicked, _ = await call(session, "click", {
                "x": text_anchor["x"], "y": text_anchor["y"], "count": 3})
            check("三击返回真实次数", clicked.get("count") == 3)
            check("三击已派发", clicked.get("dispatched") is True)

            data, _ = await call(session, "look", {"hwnd": hwnd, "image": False})
            anchor = next((e for e in data.get("elements") or []
                           if e.get("type") == "text" and "capability" in (e.get("name") or "")
                               and e.get("y", 0) > editor.get("top", 0)), None)
            check("路径拖拽锚点", anchor is not None)
            dragged, _ = await call(session, "drag_path", {"points": [
                [anchor["left"] + 2, anchor["y"]], [anchor["left"] + 12, anchor["y"]],
                [anchor["right"] - 12, anchor["y"]], [anchor["right"] - 2, anchor["y"]],
            ]})
            check("drag_path 点数与派发", dragged.get("ok") is True and dragged.get("points") == 4)
            copied = await copied_text(session)
            check("路径拖拽选中文本", bool(copied.strip()), repr(copied))

            launched, _ = await call(session, "launch_app", {
                "app": sys.executable, "args": ["-c", "import time;time.sleep(30)"]})
            pid = launched.get("launcher_pid")
            check("launch_app 安全派发子进程", launched.get("dispatched") is True and bool(pid))
            if pid:
                os.kill(pid, signal.SIGTERM)

            data, _ = await call(session, "look", {"hwnd": hwnd, "image": False})
            editor = find_el(data.get("elements") or [], "文本编辑器", "Text Editor")
            await call(session, "click", {"x": editor["x"], "y": editor["y"]})
            await call(session, "press_key", {"keys": ["ctrl", "w"]})
            await asyncio.sleep(0.8)
            cleaned = await dismiss_save_dialog(session)
            check("清理测试标签", cleaned)
            if cleaned:
                closed, _ = await call(session, "window_action", {"action": "close", "hwnd": hwnd})
                check("独立测试窗口已清理", closed.get("ok") is True)
    print(f"\n===== 结果: {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
