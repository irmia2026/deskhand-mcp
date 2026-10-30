"""
test_more.py — 第二轮试用：其余工具与场景（真实操控记事本）。

覆盖：scene_grid / verify_point / read_value / target 点击（记忆→OCR）/
type_text 键鼠路径 / scroll / drag / wait_change / window_action(move/resize)。
使用独立空白记事本窗口，结尾关闭测试标签与窗口，不选择用户现有窗口。

用法: python test_more.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client import call, find_el, dismiss_save_dialog, save_img, check, PASS, FAIL


async def main() -> int:
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "deskhand_mcp.server"],
                                   cwd=os.path.dirname(os.path.abspath(__file__)),
                                   env=dict(os.environ))
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            print("== 准备：启动独立记事本 + 新建标签 ==")
            launched, _ = await call(s, "launch_app", {
                "app": "notepad.exe", "wait_for_window": True, "timeout": 10})
            candidates = launched.get("candidate_windows") or []
            new_notepads = [w for w in candidates if "notepad" in w.get("title", "").lower()]
            window = launched.get("window") or (new_notepads[0] if len(new_notepads) == 1 else None)
            check("独立记事本窗口已识别", window is not None,
                  str(launched.get("note") or launched.get("error") or ""))
            if window is None:
                raise RuntimeError("无法确认独立记事本窗口，停止测试以避免操作用户窗口")
            hwnd = window["hwnd"]
            d, _ = await call(s, "look", {"hwnd": hwnd})
            els = d.get("elements") or []
            tabbtn = find_el(els, "添加新标签页", "New tab")
            check("起点干净（无遗留测试标签）", not any(
                marker in str(d.get("window"))
                for marker in ("deskhand-mcp-e2e", "select-me-line", "capability", "中文😀")))
            await call(s, "click", {"element": tabbtn["id"]})
            await asyncio.sleep(0.6)
            d, _ = await call(s, "look", {"window": ""})
            els = d.get("elements") or []
            editor = find_el(els, "文本编辑器", "Text Editor")
            check("拿到编辑器", editor is not None)
            # 首行写入标记，让标签标题带上可识别名（清理校验依赖它）
            d, _ = await call(s, "append_text",
                               {"text": "deskhand-mcp-e2e-验证行", "window": "",
                                "element": editor["id"]})
            check("首行标记写入", d.get("ok") is True and d.get("verified") is True)

            # 1) scene_grid
            print("== scene_grid ==")
            d, img = await call(s, "scene_grid", {"window": ""})
            check("scene_grid ok", d.get("ok") is True, str(d.get("error", ""))[:120])
            grid = d.get("grid") or {}
            check("3x3 格映射", len(grid.get("cell_centers") or {}) == 9)
            if img:
                save_img(img, "10_scene_grid.png")
            check("scene_grid 带图", img is not None)
            a1 = (grid.get("cell_centers") or {}).get("A1") or {}
            ox, oy = grid.get("origin_screen", [0, 0])
            cw, ch = grid.get("cell_size", [0, 0])
            check("A1 中心=原点+半格", a1.get("screen_x") == ox + cw // 2
                  and a1.get("screen_y") == oy + ch // 2,
                  f"A1={a1.get('screen_x')},{a1.get('screen_y')} origin={ox},{oy} cell={cw}x{ch}")
            els = d.get("elements") or []
            editor = find_el(els, "文本编辑器", "Text Editor")
            check("scene_grid 后刷新元素引用", editor is not None)

            # 2) verify_point：准星打向「加粗」按钮
            print("== verify_point ==")
            bold = find_el(els, "加粗")
            check("找到加粗按钮", bold is not None)
            d, img = await call(s, "verify_point",
                                {"x": bold["x"], "y": bold["y"], "target": "加粗按钮",
                                 "window": ""})
            check("verify_point ok", d.get("ok") is True, str(d.get("error", ""))[:120])
            check("verify_point 带图", img is not None)
            if img:
                save_img(img, "11_verify_point.png")

            # 3) read_value（编辑器当前值=首行标记）
            print("== read_value ==")
            d, _ = await call(s, "read_value", {"window": "", "element": editor["id"]})
            check("read_value ok", d.get("ok") is True, str(d.get("error", ""))[:150])
            check("read_value 读到首行标记", "deskhand-mcp-e2e" in (d.get("value") or ""),
                  repr((d.get("value") or "")[:40]))

            # 4) type_text 键鼠路径（默认在光标/选区插入，不隐式追加换行）
            print("== type_text 键鼠路径 ==")
            await call(s, "click", {"x": editor["x"], "y": editor["y"]})
            await asyncio.sleep(0.3)
            d, _ = await call(s, "type_text", {"text": "keyboard-line-123",
                                               "window": "", "focus": False})
            check("type_text ok", d.get("ok") is True, str(d.get("error", ""))[:150])
            d, _ = await call(s, "read_document", {"window": "", "element": editor["id"]})
            check("键鼠输入可读回", "keyboard-line-123" in (d.get("text") or ""),
                  repr((d.get("text") or "")[:40]))

            # 5) target 点击（UIA/记忆/OCR 确定性定位）+ ESC 收回
            print("== target 点击（确定性定位）==")
            d, _ = await call(s, "click", {"target": "查看", "window": ""})
            check("target 点击 ok", d.get("ok") is True,
                  f"{d.get('error', '')} | source={d.get('locate_source')}")
            check("首次确定性定位", d.get("locate_source") in ("uia", "ocr", "memory"),
                  str(d.get("locate_source")))
            await asyncio.sleep(0.5)
            await call(s, "press_key", {"keys": ["esc"]})
            await asyncio.sleep(0.3)
            # 真实鼠标点对 XAML 菜单按钮可能只聚焦不展开——改用元素编号走 UIA 后台展开
            d, _ = await call(s, "look", {"window": ""})
            menu_btn = find_el(d.get("elements") or [], "查看")
            d, _ = await call(s, "element_action", {"element": menu_btn["id"], "action": "expand"})
            action_result = d
            check("UIA 展开查看菜单", d.get("ok") is True, str(d)[:300])
            await asyncio.sleep(0.6)
            d, menu_img = await call(s, "look", {"window": ""})
            menu_open = find_el(d.get("elements") or [], "缩放", "状态栏", "自动换行")
            if menu_open is None and menu_img:
                save_img(menu_img, "menu_test_failure.png")
            check("查看菜单实际展开", menu_open is not None,
                  f"state={action_result.get('state_after')} budget={d.get('element_budget')} "
                  f"names={[e.get('name') for e in d.get('elements') or []]}")
            d, _ = await call(s, "press_key", {"keys": ["esc"]})
            await asyncio.sleep(0.4)

            # 5b) 第二次 target 点击必须命中记忆库
            d, _ = await call(s, "click", {"target": "查看", "window": ""})
            check("再次 target 点击 ok", d.get("ok") is True, str(d.get("error", ""))[:120])
            check("第二次 target 命中记忆库", d.get("locate_source") == "memory",
                  str(d.get("locate_source")))
            await asyncio.sleep(0.4)
            await call(s, "press_key", {"keys": ["esc"]})

            # 6) scroll：验证可见内容确实变化
            print("== scroll ==")
            d, _ = await call(s, "look", {"window": "", "image": False})
            editor = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")
            await call(s, "append_text", {
                "text": "\n" + "\n".join(f"scroll-line-{i}" for i in range(60)),
                "window": "", "element": editor["id"]})
            d, _ = await call(s, "look", {"window": "", "image": False})
            before_words = {e.get("name") for e in d.get("elements") or []}
            d, _ = await call(s, "scroll", {"direction": "down", "amount": 5,
                                            "x": editor["x"], "y": editor["y"]})
            check("scroll dispatched", d.get("ok") is True, str(d.get("error", ""))[:120])
            d, _ = await call(s, "look", {"window": "", "image": False})
            after_words = {e.get("name") for e in d.get("elements") or []}
            check("滚动后可见内容变化", before_words != after_words)
            editor = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")

            # 7) drag：用复制到的选中文本验证，并恢复系统剪贴板
            print("== drag ==")
            d, _ = await call(s, "set_value", {
                "text": "select-me-line\n第二行文字", "confirm_overwrite": True,
                "window": "", "element": editor["id"]})
            check("准备可见的拖拽文本", d.get("ok") is True and d.get("verified") is True)
            d, _ = await call(s, "look", {"window": ""})
            editor = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")
            t = next((e for e in d.get("elements") or []
                      if e.get("type") in ("text", "box")
                      and "select-me-line" in (e.get("ocr_text") or e.get("name") or "")
                      and e.get("y", 0) > editor.get("top", 0)), None)
            check("OCR 找到锚点文字", t is not None)
            if t:
                d, _ = await call(s, "drag", {"x1": t["left"] + 2, "y1": t["y"],
                                              "x2": t["right"] - 2, "y2": t["y"]})
                check("drag dispatched", d.get("ok") is True)
                d, _ = await call(s, "read_selection", {
                    "window": "", "element": editor["id"]})
                selected = d.get("text") or ""
                check("拖拽选区可精确读取", "select" in selected,
                      f"ok={d.get('ok')} text={selected!r} error={d.get('error')}")

            # 8) wait_change：状态栏字符数变化由本次输入造成
            print("== wait_change ==")
            await call(s, "click", {"x": editor["x"], "y": editor["y"]})
            await asyncio.sleep(0.2)
            d, _ = await call(s, "read_document", {"window": "", "element": editor["id"]})
            old_len = len(d.get("text") or "")
            grid2, _ = await call(s, "scene_grid", {"window": "", "image": False})
            editor = find_el(grid2.get("elements") or [], "文本编辑器", "Text Editor")
            check("等待测试使用最新元素引用", editor is not None)
            ox, oy = grid2["grid"]["origin_screen"]
            width, height = grid2["size"]
            region = [ox + 80, oy + height - 48, min(600, width - 160), 36]
            async def type_and_wait():
                await asyncio.sleep(0.3)
                await call(s, "type_text", {"text": "x", "focus": False})
            task = asyncio.create_task(type_and_wait())
            d, _ = await call(s, "wait_change", {"region": region, "timeout": 5.0})
            await task
            check("wait_change 捕获本次输入", d.get("changed") is True,
                  f"changed={d.get('changed')} elapsed={d.get('elapsed')}")
            d, _ = await call(s, "read_document", {"window": "", "element": editor["id"]})
            check("等待期间输入实际写入", len(d.get("text") or "") == old_len + 1)

            # 9) window_action：验证真实矩形和最大化状态
            print("== window_action ==")
            await call(s, "window_action", {"action": "focus", "hwnd": hwnd})
            await call(s, "window_action", {"action": "restore", "hwnd": hwnd})
            d, _ = await call(s, "window_action", {"action": "move", "hwnd": hwnd, "x": 300, "y": 200})
            check("move dispatched", d.get("ok") is True, str(d.get("error", ""))[:100])
            wins, _ = await call(s, "list_windows", {})
            moved = next(w for w in wins.get("windows") or [] if w.get("hwnd") == hwnd)
            check("move 真实坐标生效", moved["rect"][:2] == [300, 200], str(moved["rect"]))
            d, _ = await call(s, "window_action", {"action": "resize", "hwnd": hwnd, "w": 900, "h": 700})
            check("resize dispatched", d.get("ok") is True, str(d.get("error", ""))[:100])
            wins, _ = await call(s, "list_windows", {})
            resized = next(w for w in wins.get("windows") or [] if w.get("hwnd") == hwnd)
            check("resize 真实尺寸生效", resized["rect"][2] - resized["rect"][0] == 900
                  and resized["rect"][3] - resized["rect"][1] == 700, str(resized["rect"]))
            d, _ = await call(s, "look", {"window": "", "image": False})
            before_max = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")
            d, _ = await call(s, "window_action", {"action": "max", "hwnd": hwnd})
            check("max ok", d.get("ok") is True and d.get("window_state_after") == "maximized",
                  str(d.get("window_state_after")))
            await asyncio.sleep(0.4)
            d, _ = await call(s, "look", {"window": ""})
            ed2 = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")
            check("max 后编辑器仍在卡片", ed2 is not None)
            check("max 后元素坐标跟随新布局", ed2 is not None and ed2["x"] != before_max["x"],
                  f"old={before_max['x']} new={ed2['x'] if ed2 else '?'}")

            # 10) stale 防护：不存在的编号必须报错而不是盲点
            print("== stale 防护 ==")
            d, _ = await call(s, "click", {"element": "e999"})
            check("不存在编号被拒绝", d.get("ok") is False and "不存在" in str(d.get("error", "")),
                  str(d.get("error", ""))[:80])
            d, _ = await call(s, "read_document", {"window": "", "element": "e999"})
            check("过期/不存在编号读取被拒绝", d.get("ok") is False,
                  str(d.get("error", ""))[:80])

            # 清理
            print("== 清理 ==")
            d, _ = await call(s, "look", {"window": ""})
            ed3 = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")
            if ed3:
                await call(s, "click", {"x": ed3["x"], "y": ed3["y"]})
            await asyncio.sleep(0.3)
            await call(s, "press_key", {"keys": ["ctrl", "w"]})
            await asyncio.sleep(0.8)
            await dismiss_save_dialog(s)
            d, _ = await call(s, "look", {"window": ""})
            leftover_tabs = [e for e in (d.get("elements") or []) if e.get("type") == "tab"
                             and any(k in str(e.get("name")) for k in ("deskhand", "keyboard-line", "select-me"))]
            check("已关闭测试标签（不保存）", not leftover_tabs,
                  str([t.get("name") for t in leftover_tabs])[:100])
            if not leftover_tabs:
                closed, _ = await call(s, "window_action", {"action": "close", "hwnd": hwnd})
                check("独立测试窗口已清理", closed.get("ok") is True)

    print(f"\n===== 结果: {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
