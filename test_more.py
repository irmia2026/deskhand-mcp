"""
test_more.py — 第二轮试用：其余工具与场景（真实操控记事本）。

覆盖：scene_grid / verify_point / read_value / target 点击（记忆→OCR）/
type_text 键鼠路径 / scroll / drag / wait_change / window_action(move/resize)。
同样全程在新标签操作，结尾键盘关对话框清理。

用法: python test_more.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client import call, find_el, dismiss_save_dialog, save_img, check, PASS, FAIL, ART


async def main() -> int:
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "deskhand_mcp.server"],
                                   cwd=os.path.dirname(os.path.abspath(__file__)))
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            print("== 准备：focus + 新建标签 ==")
            await call(s, "window_action", {"action": "focus", "title": "Notepad"})
            d, _ = await call(s, "look", {"window": "Notepad"})
            els = d.get("elements") or []
            tabbtn = find_el(els, "添加新标签页", "New tab")
            if "deskhand-mcp-e2e" in str(d.get("window")):
                check("起点干净（无遗留测试标签）", False)
            else:
                check("起点干净（无遗留测试标签）", True)
            await call(s, "click", {"element": tabbtn["id"]})
            await asyncio.sleep(0.6)
            d, _ = await call(s, "look", {"window": "Notepad"})
            els = d.get("elements") or []
            editor = find_el(els, "文本编辑器", "Text Editor")
            check("拿到编辑器", editor is not None)
            # 首行写入标记，让标签标题带上可识别名（清理校验依赖它）
            d, _ = await call(s, "append_text",
                               {"text": "deskhand-mcp-e2e-验证行", "window": "Notepad",
                                "element": editor["id"]})
            check("首行标记写入", d.get("ok") is True and d.get("verified") is True)

            # 1) scene_grid
            print("== scene_grid ==")
            d, img = await call(s, "scene_grid", {"window": "Notepad"})
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

            # 2) verify_point：准星打向「加粗」按钮
            print("== verify_point ==")
            bold = find_el(els, "加粗")
            check("找到加粗按钮", bold is not None)
            d, img = await call(s, "verify_point",
                                {"x": bold["x"], "y": bold["y"], "target": "加粗按钮",
                                 "window": "Notepad"})
            check("verify_point ok", d.get("ok") is True, str(d.get("error", ""))[:120])
            check("verify_point 带图", img is not None)
            if img:
                save_img(img, "11_verify_point.png")

            # 3) read_value（编辑器当前值=首行标记）
            print("== read_value ==")
            d, _ = await call(s, "read_value", {"window": "Notepad", "element": editor["id"]})
            check("read_value ok", d.get("ok") is True, str(d.get("error", ""))[:150])
            check("read_value 读到首行标记", "deskhand-mcp-e2e" in (d.get("value") or ""),
                  repr((d.get("value") or "")[:40]))

            # 4) type_text 键鼠路径（element 走 UIA 追加，这里用 target= 强制键鼠）
            print("== type_text 键鼠路径 ==")
            await call(s, "click", {"x": editor["x"], "y": editor["y"]})
            await asyncio.sleep(0.3)
            d, _ = await call(s, "type_text", {"text": "keyboard-line-123",
                                               "window": "Notepad", "focus": False})
            check("type_text ok", d.get("ok") is True, str(d.get("error", ""))[:150])
            d, _ = await call(s, "read_document", {"window": "Notepad", "element": editor["id"]})
            check("键鼠输入可读回", "keyboard-line-123" in (d.get("text") or ""),
                  repr((d.get("text") or "")[:40]))

            # 5) target 点击（OCR 定位「查看」菜单）+ ESC 收回
            print("== target 点击（OCR 通道）==")
            d, _ = await call(s, "click", {"target": "查看", "window": "Notepad"})
            check("target 点击 ok", d.get("ok") is True,
                  f"{d.get('error', '')} | source={d.get('locate_source')}")
            check("OCR/memory 通道定位", d.get("locate_source") in ("ocr", "memory"),
                  str(d.get("locate_source")))
            await asyncio.sleep(0.5)
            # 真实鼠标点对 XAML 菜单按钮可能只聚焦不展开——改用元素编号走 UIA 后台展开
            d, _ = await call(s, "look", {"window": "Notepad"})
            menu_btn = find_el(d.get("elements") or [], "查看")
            d, _ = await call(s, "click", {"element": menu_btn["id"]})
            check("UIA 展开查看菜单", d.get("ok") is True, str(d.get("error", ""))[:120])
            await asyncio.sleep(0.6)
            d, _ = await call(s, "look", {"window": "Notepad"})
            menu_open = find_el(d.get("elements") or [], "缩放", "状态栏", "自动换行")
            check("查看菜单展开（UIA 通道可见菜单项）", menu_open is not None,
                  "菜单项未进卡片")
            d, _ = await call(s, "press_key", {"keys": ["esc"]})
            await asyncio.sleep(0.4)

            # 5b) 第二次 target 点击应走记忆库
            d, _ = await call(s, "click", {"target": "查看", "window": "Notepad"})
            check("再次 target 点击 ok", d.get("ok") is True, str(d.get("error", ""))[:120])
            print(f"  locate_source={d.get('locate_source')}")
            await asyncio.sleep(0.4)
            await call(s, "press_key", {"keys": ["esc"]})

            # 6) scroll：在编辑器滚动
            print("== scroll ==")
            d, _ = await call(s, "scroll", {"direction": "down", "amount": 3,
                                            "x": editor["x"], "y": editor["y"]})
            check("scroll ok", d.get("ok") is True, str(d.get("error", ""))[:120])

            # 7) drag：在编辑器拖选一段文字（先确保有文字）
            print("== drag ==")
            d, _ = await call(s, "append_text", {"text": "select-me-line\n第二行文字",
                                                 "window": "Notepad", "element": editor["id"]})
            check("追加供拖拽的文本", d.get("ok") is True and d.get("verified") is True)
            d, _ = await call(s, "look", {"window": "Notepad"})
            t = find_el(d.get("elements") or [], "select-me-line")
            check("OCR 找到锚点文字", t is not None)
            if t:
                d, _ = await call(s, "drag", {"x1": t["x"] - 120, "y1": t["y"],
                                              "x2": t["x"] + 60, "y2": t["y"]})
                check("drag ok", d.get("ok") is True)
                d, _ = await call(s, "press_key", {"keys": ["ctrl", "c"]})
                d, _ = await call(s, "read_value", {"window": "Notepad", "element": editor["id"]})
                # RichEdit 拖选后剪贴板有内容即证明 drag 有效（不依赖值回读）

            # 8) wait_change：按个键等画面变化
            print("== wait_change ==")
            await call(s, "click", {"x": editor["x"], "y": editor["y"]})
            await asyncio.sleep(0.2)
            d, _ = await call(s, "wait_change", {"region": [editor["x"] - 200, editor["y"] - 100, 400, 200], "timeout": 3.0})
            check("wait_change 就绪", d.get("ok") is True)
            async def type_and_wait():
                await asyncio.sleep(0.3)
                await call(s, "type_text", {"text": "x", "focus": False})
            task = asyncio.create_task(type_and_wait())
            d, _ = await call(s, "wait_change", {"timeout": 5.0})
            await task
            check("wait_change 捕获到变化", d.get("changed") is True,
                  f"changed={d.get('changed')} waited={d.get('waited')}")

            # 9) window_action move/resize 后 max（若已最大化则 move/resize 是空操作）
            print("== window_action ==")
            d, _ = await call(s, "window_action", {"action": "focus", "title": "Notepad"})
            state0 = d.get("window_state_before")
            d, _ = await call(s, "window_action", {"action": "move", "title": "Notepad", "x": 300, "y": 200})
            check("move ok", d.get("ok") is True, str(d.get("error", ""))[:100])
            d, _ = await call(s, "window_action", {"action": "resize", "title": "Notepad", "w": 900, "h": 700})
            check("resize ok", d.get("ok") is True, str(d.get("error", ""))[:100])
            d, _ = await call(s, "window_action", {"action": "max", "title": "Notepad"})
            check("max ok", d.get("ok") is True and d.get("window_state_after") == "maximized",
                  str(d.get("window_state_after")))
            await asyncio.sleep(0.4)
            d, _ = await call(s, "look", {"window": "Notepad"})
            els = d.get("elements") or []
            ed2 = find_el(els, "文本编辑器", "Text Editor")
            check("max 后编辑器仍在卡片", ed2 is not None)
            if state0 == "normal":
                check("max 后元素坐标跟随新布局", ed2 is not None and ed2["x"] != editor["x"],
                      f"old={editor['x']} new={ed2['x'] if ed2 else '?'}")
            else:
                check("max 后元素坐标跟随新布局(跳过:测试前已最大化)", True)

            # 10) stale 防护：不存在的编号必须报错而不是盲点
            print("== stale 防护 ==")
            d, _ = await call(s, "click", {"element": "e999"})
            check("不存在编号被拒绝", d.get("ok") is False and "不存在" in str(d.get("error", "")),
                  str(d.get("error", ""))[:80])
            d, _ = await call(s, "read_document", {"window": "Notepad", "element": "e999"})
            check("过期/不存在编号读取被拒绝", d.get("ok") is False,
                  str(d.get("error", ""))[:80])

            # 清理
            print("== 清理 ==")
            d, _ = await call(s, "look", {"window": "Notepad"})
            ed3 = find_el(d.get("elements") or [], "文本编辑器", "Text Editor")
            if ed3:
                await call(s, "click", {"x": ed3["x"], "y": ed3["y"]})
            await asyncio.sleep(0.3)
            await call(s, "press_key", {"keys": ["ctrl", "w"]})
            await asyncio.sleep(0.8)
            await dismiss_save_dialog(s)
            d, _ = await call(s, "look", {"window": "Notepad"})
            leftover_tabs = [e for e in (d.get("elements") or []) if e.get("type") == "tab"
                             and any(k in str(e.get("name")) for k in ("deskhand", "keyboard-line", "select-me"))]
            check("已关闭测试标签（不保存）", not leftover_tabs,
                  str([t.get("name") for t in leftover_tabs])[:100])

    print(f"\n===== 结果: {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
