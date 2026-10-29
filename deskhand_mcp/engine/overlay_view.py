"""Pure Pillow renderer for the Deskhand agent overlay."""

from __future__ import annotations

import functools
import os
import time

from PIL import Image, ImageDraw, ImageFont

PRIMARY = (43, 84, 229)  # irmia.me CSS #2b54e5
PRIMARY_LIGHT = (112, 148, 255)

_LABELS = {
    "look": "观察界面", "scene_grid": "分析画面", "click": "点击",
    "verify_point": "确认落点", "type_text": "输入文本", "read_value": "读取控件",
    "read_document": "读取文档", "append_text": "追加文本", "set_value": "覆盖文本",
    "press_key": "发送按键", "scroll": "滚动", "drag": "拖拽",
    "drag_path": "路径拖拽", "wait_change": "等待变化", "window_action": "窗口操作",
    "list_windows": "枚举窗口", "list_apps": "枚举应用", "launch_app": "启动应用",
    "element_action": "原生控件动作", "agent_control": "Agent 状态",
}
_LABELS_EN = {
    "look": "Observe", "scene_grid": "Analyze", "click": "Click",
    "verify_point": "Verify point", "type_text": "Type", "read_value": "Read control",
    "read_document": "Read document", "append_text": "Append", "set_value": "Overwrite",
    "press_key": "Key press", "scroll": "Scroll", "drag": "Drag",
    "drag_path": "Drag path", "wait_change": "Wait for change", "window_action": "Window",
    "list_windows": "List windows", "list_apps": "List apps", "launch_app": "Launch app",
    "element_action": "Native action", "agent_control": "Agent status",
}
_CJK_AVAILABLE = False


@functools.lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    global _CJK_AVAILABLE
    root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        os.path.join(root, "Fonts", "msyhbd.ttc" if bold else "msyh.ttc"),
        os.path.join(root, "Fonts", "Dengb.ttf" if bold else "Deng.ttf"),
        os.path.join(root, "Fonts", "simhei.ttf"),
        os.path.join(root, "Fonts", "simsunb.ttf" if bold else "simsun.ttc"),
    ]
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size, index=0)
            _CJK_AVAILABLE = True
            return font
        except Exception:
            pass
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def event_text(tool: str, args: dict) -> tuple[str, str]:
    label = _LABELS.get(tool, tool)
    if tool == "type_text" or tool in ("append_text", "set_value"):
        return label, f"{len(str(args.get('text', '')))} 字符"
    if tool == "drag_path":
        return label, f"{len(args.get('points') or [])} 个路径点"
    if tool == "press_key":
        return label, "+".join(str(v) for v in args.get("keys") or [])[:36]
    if tool == "launch_app":
        return label, str(args.get("app", ""))[:36]
    for key in ("element", "target", "window", "title", "action", "direction"):
        value = args.get(key)
        if value:
            return label, str(value)[:36]
    if args.get("x") is not None and args.get("y") is not None:
        return label, f"({args['x']}, {args['y']})"
    return label, ""


@functools.lru_cache(maxsize=24)
def _edge_rgba(width: int, height: int, depth: int) -> Image.Image:
    sw, sh = max(1, width // 4), max(1, height // 4)
    depth_small = max(1.0, depth / 4)
    values = []
    for y in range(sh):
        for x in range(sw):
            distance = min(x, y, sw - 1 - x, sh - 1 - y) / depth_small
            t = max(0.0, 1.0 - distance)
            values.append(int(96 * t ** 2.4))
    mask = Image.new("L", (sw, sh))
    mask.putdata(values)
    mask = mask.resize((width, height), Image.Resampling.BILINEAR)
    edge = Image.new("RGBA", (width, height), (*PRIMARY, 0))
    edge.putalpha(mask)
    return edge


def render_overlay(size: tuple[int, int], events: list[dict],
                   now: float | None = None, scale: float = 1.0) -> Image.Image:
    now = time.monotonic() if now is None else now
    scale = max(0.75, min(float(scale), 4.0))
    w, h = size

    def s(value: float) -> int:
        return max(1, int(round(value * scale)))

    depth = min(s(96), max(12, min(w, h) // 3))
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    image.alpha_composite(_edge_rgba(w, h, depth))
    draw = ImageDraw.Draw(image)

    title_font = _font(s(20), True)
    cjk = _CJK_AVAILABLE
    panel_w = min(s(480), max(100, w - s(40)))
    panel_h = s(68)
    px, py = (w - panel_w) // 2, s(24)
    draw.rounded_rectangle((px, py, px + panel_w, py + panel_h), radius=s(18),
                           fill=(10, 20, 48, 232), outline=(*PRIMARY, 220), width=s(1))
    draw.ellipse((px + s(21), py + s(23), px + s(35), py + s(37)),
                 fill=(*PRIMARY_LIGHT, 255))
    draw.text((px + s(49), py + s(9)), "DESKHAND  //  AGENT ACTIVE",
              font=title_font, fill=(235, 241, 255, 255))
    subtitle = "电脑操控中  ·  按 Esc 暂停" if cjk else "Agent active  ·  Esc to pause"
    draw.text((px + s(49), py + s(40)), subtitle,
              font=_font(s(14)), fill=(164, 187, 235, 255))
    draw.line((px + panel_w - s(57), py + s(16),
               px + panel_w - s(57), py + panel_h - s(16)),
              fill=(*PRIMARY, 200), width=s(1))
    draw.text((px + panel_w - s(47), py + s(24)), "LIVE",
              font=_font(s(12), True), fill=(*PRIMARY_LIGHT, 255))

    visible = list(events[-4:])
    card_w, card_h, gap = s(250), s(37), s(5)
    bottom = h - s(30)
    for index, event in enumerate(visible):
        top = bottom - (len(visible) - index) * (card_h + gap)
        age = max(0.0, now - float(event.get("created", now)))
        progress = max(0.0, min(1.0, age / 0.28))
        slide = 1 - (1 - progress) ** 3
        left = s(30) - int((1 - slide) * s(18))
        status = event.get("status", "running")
        accent = {
            "running": (*PRIMARY_LIGHT, 255), "success": (55, 214, 164, 255),
            "failed": (255, 102, 125, 255), "paused": (245, 184, 76, 255),
        }.get(status, (*PRIMARY_LIGHT, 255))
        draw.rounded_rectangle((left, top, left + card_w, top + card_h), radius=s(9),
                               fill=(10, 20, 44, 226), outline=(65, 93, 170, 220), width=s(1))
        draw.rounded_rectangle((left, top, left + s(3), top + card_h), radius=s(1), fill=accent)
        title = event.get("label") if cjk else _LABELS_EN.get(event.get("tool"), "Agent action")
        title = (title or event.get("tool", ""))[:8]
        detail = (event.get("detail", "") if cjk else "")[:8]
        draw.text((left + s(12), top + s(9)), title, font=_font(s(11), True),
                  fill=(230, 238, 253, 255))
        if detail:
            draw.text((left + s(82), top + s(10)), detail, font=_font(s(9)),
                      fill=(158, 180, 215, 255))
        if cjk:
            state_text = {"running": "执行", "success": "完成",
                          "failed": "失败", "paused": "暂停"}.get(status, status)
        else:
            state_text = {"running": "Run", "success": "Done",
                          "failed": "Fail", "paused": "Pause"}.get(status, status)
        duration = event.get("duration")
        if duration is not None:
            state_text += f" {float(duration):.2f}s"
        draw.text((left + card_w - s(80), top + s(10)), state_text,
                  font=_font(s(9)), fill=accent)
    return image


def premultiplied_bgra(image: Image.Image) -> bytes:
    from PIL import ImageChops

    *_, alpha = image.split()
    premultiplied = ImageChops.multiply(
        image, Image.merge("RGBA", (alpha, alpha, alpha, alpha)))
    premultiplied.putalpha(alpha)
    return premultiplied.tobytes("raw", "BGRA")
