"""Isolated real Windows fixture: never touches the user's existing editor tabs."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import traceback

import win32api
import win32con
import win32gui
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from deskhand_mcp.engine.desktop import _ensure_dpi_aware
from test_client import FAIL, PASS, call, check

ROOT = os.path.dirname(os.path.abspath(__file__))
TITLE = f"Deskhand workflow fixture {os.getpid()}"
STATE = {}


async def tests():
    with tempfile.TemporaryDirectory(prefix="deskhand-workflows-") as directory:
        env = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            DESKHAND_DATA_DIR=directory,
            DESKHAND_IMAGE_OUTPUT="off",
            DESKHAND_OVERLAY_PIPE=rf"\\.\pipe\deskhand_workflow_{os.getpid()}",
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deskhand_mcp.server"],
            cwd=ROOT,
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                try:
                    data, image = await call(
                        s,
                        "look",
                        {
                            "hwnd": STATE["main"],
                            "source": "uia",
                            "image": True,
                            "limit": 2,
                        },
                    )
                    check("native UIA observation", data.get("ok") is True, str(data))
                    check("text-only profile returns no image", image is None)
                    found, _ = await call(
                        s,
                        "find_element",
                        {"query": "姓名", "control_type": "input", "image": False},
                    )
                    check(
                        "find editable field without screenshot",
                        found.get("total") == 1,
                        str(found),
                    )
                    element = found["candidates"][0]["id"]
                    written, _ = await call(
                        s,
                        "set_value",
                        {
                            "element": element,
                            "text": "seed-中文😀",
                            "confirm_overwrite": True,
                        },
                    )
                    check(
                        "exact Chinese and emoji SetValue",
                        written.get("verified") is True,
                        str(written),
                    )
                    old, _ = await call(s, "read_value", {"element": element})
                    check(
                        "exact field readback",
                        old.get("value") == "seed-中文😀",
                        str(old),
                    )
                    await call(s, "click", {"element": element})
                    await call(s, "press_key", {"keys": ["ctrl", "a"]})
                    typed, _ = await call(
                        s, "type_text", {"text": "替换 selected 😀", "focus": "never"}
                    )
                    after, _ = await call(s, "read_value", {"element": element})
                    check(
                        "typing replaces selection without implicit newline",
                        typed.get("ok") is True
                        and after.get("value") == "替换 selected 😀",
                        str({"typed": typed, "after": after}),
                    )
                    app, _ = await call(
                        s,
                        "append_text",
                        {"element": element, "text": "尾部", "separator": "|"},
                    )
                    final, _ = await call(s, "read_value", {"element": element})
                    check(
                        "explicit append remains exact",
                        app.get("verified") is True
                        and final.get("value") == "替换 selected 😀|尾部",
                        str(final),
                    )
                    duplicated, _ = await call(
                        s, "find_element", {"query": "重复", "image": False}
                    )
                    check(
                        "duplicate labels require choosing",
                        duplicated.get("ambiguous") is True
                        and "suggested_click" not in duplicated,
                        str(duplicated),
                    )
                    # Unrelated windows share a process but not an owner chain.
                    await call(
                        s, "window_action", {"action": "focus", "hwnd": STATE["other"]}
                    )
                    rejected, _ = await call(
                        s, "type_text", {"text": "MUST NOT TYPE", "focus": "never"}
                    )
                    check(
                        "wrong foreground input blocked",
                        rejected.get("ok") is False,
                        str(rejected),
                    )
                    unchanged, _ = await call(s, "read_value", {"element": element})
                    check(
                        "blocked input leaves value intact",
                        unchanged.get("value") == final.get("value"),
                        str(unchanged),
                    )
                    await call(
                        s, "window_action", {"action": "focus", "hwnd": STATE["main"]}
                    )
                    for tool, args in (
                        ("press_key", {"keys": ["a"]}),
                        ("scroll", {"direction": "down", "x": 150, "y": 250}),
                        ("drag_path", {"points": [[150, 250], [180, 250]]}),
                    ):
                        r, _ = await call(
                            s, tool, {**args, "window": "NO-SUCH-WINDOW-EXACT-TEST"}
                        )
                        check(
                            f"{tool} missing target blocks",
                            r.get("ok") is False,
                            str(r),
                        )
                    bad, _ = await call(
                        s, "click", {"x": STATE["other_x"] + 100, "y": 240}
                    )
                    check(
                        "outside target click blocked", bad.get("ok") is False, str(bad)
                    )
                    # A child-owned modal is legitimate even if it extends past the
                    # parent's client rectangle. Rebind that actual returned hwnd.
                    windows, _ = await call(s, "list_windows", {})
                    check(
                        "fixture windows enumerated by MCP",
                        any(w["hwnd"] == STATE["main"] for w in windows["windows"]),
                    )
                    await call(
                        s,
                        "look",
                        {"hwnd": STATE["main"], "source": "uia", "image": False},
                    )
                    moved, _ = await call(
                        s,
                        "window_action",
                        {"action": "move", "hwnd": STATE["main"], "x": 240, "y": 240},
                    )
                    stale, _ = await call(s, "click", {"x": 300, "y": 300})
                    check(
                        "window move invalidates old coordinates",
                        moved.get("ok") is True and stale.get("ok") is False,
                        str(stale),
                    )
                    await call(
                        s,
                        "look",
                        {"hwnd": STATE["main"], "source": "uia", "image": False},
                    )
                    refreshed, _ = await call(
                        s,
                        "find_element",
                        {"query": "姓名", "control_type": "input", "image": False},
                    )
                    readback, _ = await call(
                        s, "read_value", {"element": refreshed["candidates"][0]["id"]}
                    )
                    check(
                        "reobserve recovers after window move",
                        readback.get("value") == final.get("value"),
                        str(readback),
                    )
                finally:
                    await call(s, "agent_control", {"action": "exit"})


def main():
    _ensure_dpi_aware()

    def handler(hwnd, message, wparam, lparam):
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = handler
    wc.lpszClassName = TITLE
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.hbrBackground = win32con.COLOR_WINDOW + 1
    win32gui.RegisterClass(wc)
    STATE["main"] = win32gui.CreateWindowEx(
        0,
        TITLE,
        TITLE,
        win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE,
        120,
        180,
        700,
        520,
        0,
        0,
        wc.hInstance,
        None,
    )
    win32gui.CreateWindowEx(
        0,
        "STATIC",
        "姓名",
        win32con.WS_CHILD | win32con.WS_VISIBLE,
        20,
        20,
        90,
        24,
        STATE["main"],
        1000,
        wc.hInstance,
        None,
    )
    STATE["edit"] = win32gui.CreateWindowEx(
        win32con.WS_EX_CLIENTEDGE,
        "EDIT",
        "seed",
        win32con.WS_CHILD
        | win32con.WS_VISIBLE
        | win32con.WS_TABSTOP
        | win32con.ES_AUTOHSCROLL,
        120,
        20,
        480,
        32,
        STATE["main"],
        1001,
        wc.hInstance,
        None,
    )
    for i in range(2):
        win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "重复",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            20 + i * 150,
            100,
            120,
            35,
            STATE["main"],
            1100 + i,
            wc.hInstance,
            None,
        )
    STATE["other_x"] = 1100
    STATE["other"] = win32gui.CreateWindowEx(
        0,
        "STATIC",
        "Deskhand unrelated fixture",
        win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE,
        1100,
        180,
        500,
        400,
        0,
        0,
        wc.hInstance,
        None,
    )
    errors = []

    def worker():
        try:
            asyncio.run(tests())
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            win32gui.PostMessage(STATE["main"], win32con.WM_CLOSE, 0, 0)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    win32gui.PumpMessages()
    thread.join(5)
    if win32gui.IsWindow(STATE["other"]):
        win32gui.DestroyWindow(STATE["other"])
    if errors:
        print("\n".join(errors))
    print(
        json.dumps(
            {"passed": len(PASS), "failed": len(FAIL), "errors": errors},
            ensure_ascii=False,
        )
    )
    return 1 if FAIL or errors else 0


if __name__ == "__main__":
    sys.exit(main())
