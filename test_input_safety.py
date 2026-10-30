from __future__ import annotations

import asyncio
import os
import sys

import pywintypes
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from test_client import call, check, dismiss_save_dialog, find_el, PASS, FAIL
from deskhand_mcp.engine import input as inp
from deskhand_mcp.engine.clipboard import ClipboardBackup

ROOT = os.path.dirname(os.path.abspath(__file__))


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "deskhand_mcp.server"], cwd=ROOT,
        env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            launched, _ = await call(session, "launch_app", {
                "app": "notepad.exe", "wait_for_window": True, "timeout": 10})
            candidates = launched.get("candidate_windows") or []
            new_notepads = [w for w in candidates if "notepad" in w.get("title", "").lower()]
            window = launched.get("window") or (new_notepads[0] if len(new_notepads) == 1 else None)
            check("独立记事本窗口已识别", window is not None)
            if window is None:
                raise RuntimeError("无法确认独立记事本窗口，停止测试")
            hwnd = window["hwnd"]
            await call(session, "window_action", {
                "action": "focus", "hwnd": hwnd, "title": "Notepad"})
            data, _ = await call(session, "look", {"hwnd": hwnd, "image": False})
            tab = find_el(data.get("elements") or [], "添加新标签页", "New tab")
            if tab is not None:
                await call(session, "click", {"element": tab["id"]})
            else:
                await call(session, "press_key", {"keys": ["ctrl", "t"]})
            await asyncio.sleep(0.5)
            data, _ = await call(session, "look", {"window": "", "image": False})
            editor = find_el(data.get("elements") or [], "文本编辑器", "Text Editor")
            await call(session, "click", {"x": editor["x"], "y": editor["y"]})
            typed, _ = await call(session, "type_text", {
                "text": "中文😀\n第二行", "focus": False})
            check("默认 Unicode 不碰剪贴板", typed.get("input_method") == "unicode"
                  and typed.get("clipboard_restored") is None)
            data, _ = await call(session, "read_document", {
                "window": "", "element": editor["id"]})
            check("中文和 emoji 逐字读回", "中文😀\n第二行" in
                  (data.get("text") or "").replace("\r\n", "\n").replace("\r", "\n"))

            try:
                before = ClipboardBackup._read_all()
            except (TypeError, pywintypes.error):
                try:
                    inp.type_text("clip-中文", method="clipboard")
                    check("不可备份格式安全拒绝", False, "未拒绝不可备份的剪贴板")
                except RuntimeError as exc:
                    check("不可备份格式安全拒绝", "无法完整备份" in str(exc), str(exc))
            else:
                pasted = inp.type_text("clip-中文", method="clipboard")
                after = ClipboardBackup._read_all()
                check("可选粘贴完整恢复剪贴板", pasted.get("clipboard_restored") is True
                      and before == after)
                data, _ = await call(session, "read_document", {
                    "window": "", "element": editor["id"]})
                check("可选粘贴文本实际写入", "clip-中文" in (data.get("text") or ""))

            await call(session, "window_action", {"action": "focus", "title": "Kimi Code"})
            wins, _ = await call(session, "list_windows", {})
            foreground = next(w for w in wins.get("windows") or [] if w.get("foreground"))
            check("遮挡窗口已置前", "Kimi Code" in foreground.get("title", ""))
            blocked, _ = await call(session, "click", {
                "x": editor["x"], "y": editor["y"], "window": "", "focus": "never"})
            check("遮挡目标拒绝真实点击", blocked.get("ok") is False
                  and blocked.get("focus_required") is True)
            await call(session, "window_action", {"action": "focus", "title": "Notepad"})
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
