from __future__ import annotations

import asyncio
import os
import sys
import threading
import traceback
import tkinter as tk

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from test_client import call, check, save_img, PASS, FAIL

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = {"events": [], "lock": threading.Lock(), "icons": {}}
TEXT_TITLE = f"Deskhand Pixel Text Fixture {os.getpid()}"
ICON_TITLE = f"Deskhand Icon Only Fixture {os.getpid()}"


def record(action: str) -> None:
    with STATE["lock"]:
        STATE["events"].append(action)


def build_text_window(root: tk.Tk) -> None:
    root.title(TEXT_TITLE)
    root.geometry("800x500+50+120")
    root.attributes("-topmost", True)
    root.configure(bg="#101820")
    canvas = tk.Canvas(root, bg="#101820", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    for x, label, action, color in (
        (60, "LAUNCH", "launch", "#2b54e5"),
        (300, "SETTINGS", "settings", "#226b80"),
        (540, "CANCEL", "cancel", "#774455"),
    ):
        item = canvas.create_rectangle(x, 120, x + 190, 240, fill=color,
                                       outline="#d5e5ff", width=4)
        text_item = canvas.create_text(x + 95, 180, text=label, fill="white",
                                       font=("Segoe UI", 24, "bold"))
        canvas.tag_bind(item, "<Button-1>", lambda _e, a=action: record(a))
        canvas.tag_bind(text_item, "<Button-1>", lambda _e, a=action: record(a))
    STATE["text_hwnd"] = root.winfo_id()


def build_icon_window(root: tk.Tk, parent: tk.Tk) -> None:
    window = tk.Toplevel(parent)
    window.title(ICON_TITLE)
    window.geometry("800x500+900+120")
    window.attributes("-topmost", True)
    window.configure(bg="#101820")
    canvas = tk.Canvas(window, bg="#101820", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    rectangle = canvas.create_rectangle(60, 120, 200, 260, fill="#2b54e5",
                                        outline="#d5e5ff", width=4)
    circle = canvas.create_oval(300, 120, 440, 260, fill="#167d78",
                                outline="#d5e5ff", width=4)
    diamond = canvas.create_polygon(610, 120, 680, 190, 610, 260, 540, 190,
                                    fill="#7755aa", outline="#d5e5ff", width=4)
    canvas.tag_bind(rectangle, "<Button-1>", lambda _e: record("rectangle"))
    canvas.tag_bind(circle, "<Button-1>", lambda _e: record("circle"))
    canvas.tag_bind(diamond, "<Button-1>", lambda _e: record("diamond"))
    canvas.create_rectangle(100, 420, 760, 428, fill="#42546a", outline="")
    handle = canvas.create_oval(110, 400, 150, 440, fill="#a9c7ff",
                                outline="#e5f0ff", width=3)
    drag = {"x": 130}

    def motion(event):
        drag["x"] = max(130, min(730, event.x))
        canvas.coords(handle, drag["x"] - 20, 400, drag["x"] + 20, 440)
        record(f"drag:{drag['x']}")

    canvas.tag_bind(handle, "<Button-1>", lambda _e: record("slider_down"))
    canvas.bind("<B1-Motion>", motion)
    STATE["icons"] = {
        "hwnd": window.winfo_id(), "canvas": canvas, "handle": handle,
        "circle": (430, 190), "slider": (130, 420),
    }
    STATE["icon_window"] = window


def update_icon_coordinates(hwnd: int) -> None:
    import win32gui
    canvas = STATE["icons"]["canvas"]
    canvas_hwnd = canvas.winfo_id()
    scale = canvas.winfo_fpixels("1i") / 96.0
    print("canvas_dpi_scale", scale)
    STATE["icons"]["circle"] = tuple(
        round(v * scale) for v in win32gui.ClientToScreen(canvas_hwnd, (430, 190)))
    STATE["icons"]["slider"] = tuple(
        round(v * scale) for v in win32gui.ClientToScreen(canvas_hwnd, (130, 420)))


async def run_tests() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "deskhand_mcp.server"], cwd=ROOT,
        env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            windows, _ = await call(session, "list_windows", {})
            text_hwnd = next(w["hwnd"] for w in windows.get("windows") or []
                             if w.get("title") == TEXT_TITLE)
            icon_hwnd = next(w["hwnd"] for w in windows.get("windows") or []
                             if w.get("title") == ICON_TITLE)
            STATE["icons"]["hwnd"] = icon_hwnd
            update_icon_coordinates(icon_hwnd)
            print("fixture_coordinates", STATE["icons"]["circle"], STATE["icons"]["slider"])
            await call(session, "window_action", {"action": "focus", "hwnd": text_hwnd})
            data, image = await call(session, "look", {"hwnd": text_hwnd})
            if image:
                save_img(image, "canvas_text_look.png")
            check("像素文字窗口 UIA 基本为空", not data.get("uia_elements"),
                  str(data.get("uia_elements")))
            print("text_elements", data.get("elements"))
            ocr_names = [(e.get("ocr_text") or e.get("name", ""))
                         for e in data.get("elements") or []]
            check("自绘文字可由 OCR 发现", any("LAUNCH" in name for name in ocr_names),
                  str(ocr_names))
            clicked, _ = await call(session, "click", {"target": "LAUNCH", "window": TEXT_TITLE})
            await asyncio.sleep(0.3)
            check("OCR 点击真实命中自绘按钮", "launch" in STATE["events"],
                  f"result={clicked} events={STATE['events']}")

            first, _ = await call(session, "look", {"hwnd": text_hwnd, "limit": 2})
            cursor = first.get("page", {}).get("next_cursor")
            check("元素分页游标存在", bool(cursor), str(first.get("page")))
            second, _ = await call(session, "look", {"cursor": cursor, "limit": 2})
            first_ids = {e["id"] for e in first.get("elements") or []}
            second_ids = {e["id"] for e in second.get("elements") or []}
            check("翻页元素不重复且代次一致", first_ids.isdisjoint(second_ids)
                  and first.get("target", {}).get("generation")
                  == second.get("target", {}).get("generation"))
            raw, image = await call(session, "look", {"hwnd": text_hwnd, "view": "raw"})
            if image:
                save_img(image, "canvas_raw.png")
            check("raw 返回未标注原图", raw.get("view") == "raw" and image is not None)
            grid_view, image = await call(session, "look", {"hwnd": text_hwnd, "view": "grid"})
            if image:
                save_img(image, "canvas_look_grid.png")
            grid_info = grid_view.get("grid") or {}
            mapping = grid_view.get("image_mapping") or {}
            check("look 网格视图直读图内坐标", grid_view.get("view") == "grid"
                  and grid_info.get("kind") == "image_coordinates"
                  and grid_info.get("coordinate_space") == "image_pixels"
                  and grid_info.get("image_size") == grid_view.get("image_size")
                  and grid_info.get("spacing_px", 0) > 0)
            gw, gh = grid_info.get("image_size", [1, 1])
            gl, gt, gr, gb = grid_info.get("screen_rect", [0, 0, 0, 0])
            check("网格刻度保留精确屏幕映射", bool(grid_view.get("image_id"))
                  and grid_info.get("screen_rect") == mapping.get("screen_rect")
                  and all(line["screen_x"] == gl + int(line["image_x"] * (gr - gl) / gw)
                          for line in grid_info.get("x_lines") or [])
                  and all(line["screen_y"] == gt + int(line["image_y"] * (gb - gt) / gh)
                          for line in grid_info.get("y_lines") or []))
            default_look, _ = await call(session, "look", {})
            check("look 默认绑定前台窗口", default_look.get("target", {}).get("hwnd") == text_hwnd,
                  str(default_look.get("target")))

            await call(session, "window_action", {"action": "focus", "hwnd": icon_hwnd})
            data, image = await call(session, "look", {"hwnd": icon_hwnd})
            if image:
                save_img(image, "canvas_icons_look.png")
            # Tk rooty is DPI-virtualized in the fixture process. The server's
            # physical client boundary is the coordinate system of its elements.
            client_top = data["client_rect"][1]
            ocr_content = [e for e in data.get("elements") or []
                           if e.get("source") == "ocr" and e.get("type") == "text"
                           and e.get("y", 0) >= client_top
                           and (e.get("name") or "").strip() not in ("囗", "0")]
            check("纯图形窗口没有可读控件文本", not ocr_content,
                  str([e.get("name") for e in ocr_content]))
            check("纯图形窗口由 CV 提供候选元素", data.get("cv_kept", 0) >= 2,
                  f"cv={data.get('cv_boxes')} kept={data.get('cv_kept')}")
            cv_elements = [e for e in data.get("elements") or [] if e.get("source") == "cv"]
            print("cv_elements", cv_elements)
            candidates = [e for e in cv_elements
                          if 70 <= e["right"] - e["left"] <= 300
                          and 70 <= e["bottom"] - e["top"] <= 300
                          and 0.65 <= (e["right"] - e["left"]) / max(1, e["bottom"] - e["top"]) <= 1.5]
            candidates.sort(key=lambda e: e["x"])
            target = candidates[1] if len(candidates) >= 2 else None
            check("CV 圆形图标进入元素卡片", target is not None, str(target))
            if target is None:
                raise AssertionError("未找到圆形图标候选")
            circle_x, circle_y = target["x"], target["y"]
            result, _ = await call(session, "click", {"element": target["id"]})
            await asyncio.sleep(0.3)
            check("无文字图标点击真实命中", "circle" in STATE["events"],
                  f"result={result} events={STATE['events']}")

            # A weak-vision host can select by supplied attributes; no visual
            # coordinate estimate or fixture coordinates are used for input.
            found, atlas = await call(session, "find_element", {
                "shape": "circle", "color": "cyan", "source": "cv"})
            check("颜色形状定位唯一无文字图标", found.get("total") == 1, str(found))
            if atlas:
                save_img(atlas, "grounding_candidates.png")
            candidate = found["candidates"][0]
            detail, image = await call(session, "inspect_region", {"element": candidate["id"], "zoom": 3})
            if image:
                save_img(image, "grounding_detail.png")
            check("局部放大附精确映射", bool(detail.get("image_id")) and bool(detail.get("focus_point")))
            STATE["events"].clear()
            point = detail["focus_point"]
            mapped, _ = await call(session, "click", {"image_id": detail["image_id"],
                                                       "x": round(point["image_x"]), "y": round(point["image_y"])})
            check("放大图坐标点击真实命中", mapped.get("ok") is True and "circle" in STATE["events"], str(mapped))
            nested, _ = await call(session, "inspect_region", {"image_id": detail["image_id"], "cell": "B2"})
            check("递归放大保持正确屏幕范围", nested.get("screen_rect", [0,0,0,0])[0] > detail["screen_rect"][0])

            grid, image = await call(session, "scene_grid", {"hwnd": icon_hwnd})
            if image:
                save_img(image, "canvas_icons_grid.png")
            check("无文字 UI 提供 3x3 视觉网格", len(grid.get("grid", {}).get("cell_centers") or {}) == 9)
            stale, _ = await call(session,"click",{"image_id":detail["image_id"],"x":10,"y":10})
            check("新快照拒绝旧图像坐标", stale.get("ok") is False and stale.get("stale") is True)
            point, image = await call(session, "verify_point", {"x": circle_x, "y": circle_y,
                                                                 "window": ICON_TITLE})
            if image:
                save_img(image, "canvas_verify.png")
            check("无文字落点可视觉复核", point.get("ok") is True and image is not None,
                  str(point))
            missed, _ = await call(session, "click", {"target": "LAUNCH", "window": ICON_TITLE})
            check("无文字目标不猜测坐标", missed.get("ok") is False)

            slider_candidates = [e for e in cv_elements
                                 if e.get("y", 0) > circle_y + 100
                                 and e["right"] - e["left"] < 120
                                 and e["bottom"] - e["top"] < 120]
            slider_candidates.sort(key=lambda e: e["x"])
            if not slider_candidates:
                raise AssertionError("未找到滑块手柄候选")
            slider_x, slider_y = slider_candidates[0]["x"], slider_candidates[0]["y"]
            region = [slider_x - 40, slider_y - 50, 500, 100]

            async def drag_later():
                await asyncio.sleep(0.3)
                return await call(session, "drag_path", {"points": [
                    [slider_x + i * 35, slider_y] for i in range(9)
                ], "step_ms": 35})

            task = asyncio.create_task(drag_later())
            changed, _ = await call(session, "wait_change", {"region": region, "timeout": 4})
            dragged, _ = await task
            check("自绘滑块变化可被等待捕获", changed.get("changed") is True,
                  str(changed))
            drag_events = [e for e in STATE["events"] if e.startswith("drag:")]
            first_x = int(drag_events[0].split(":")[1]) if drag_events else slider_x
            final_x = int(drag_events[-1].split(":")[1]) if drag_events else first_x
            check("无文字滑块拖拽真实生效", final_x - first_x >= 150,
                  f"result={dragged} delta={final_x - first_x} events={STATE['events'][-8:]}")
            await call(session, "agent_control", {"action": "exit"})


def main() -> int:
    root = tk.Tk()
    build_text_window(root)
    build_icon_window(root, root)
    root.update()
    import win32con
    import win32gui
    STATE["text_hwnd"] = win32gui.GetAncestor(root.winfo_id(), win32con.GA_ROOT)
    STATE["icons"]["hwnd"] = win32gui.GetAncestor(
        STATE["icon_window"].winfo_id(), win32con.GA_ROOT)
    canvas = STATE["icons"]["canvas"]
    window = STATE["icon_window"]
    rect = win32gui.GetWindowRect(STATE["icons"]["hwnd"])
    scale_x = (rect[2] - rect[0]) / window.winfo_width()
    scale_y = (rect[3] - rect[1]) / window.winfo_height()
    client_x = rect[0] + (canvas.winfo_rootx() - window.winfo_rootx()) * scale_x
    client_y = rect[1] + (canvas.winfo_rooty() - window.winfo_rooty()) * scale_y
    STATE["icons"]["circle"] = (round(client_x + 430 * scale_x), round(client_y + 190 * scale_y))
    STATE["icons"]["slider"] = (round(client_x + 130 * scale_x), round(client_y + 420 * scale_y))
    errors = []

    def worker() -> None:
        try:
            asyncio.run(run_tests())
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            root.after(0, root.destroy)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    root.mainloop()
    thread.join(5)
    if errors:
        print("\n".join(errors))
    print(f"\n===== 结果: {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    return 1 if errors or FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
