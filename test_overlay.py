from __future__ import annotations

import os
import time

from PIL import Image

from deskhand_mcp.engine import desktop, input as inp, overlay
from deskhand_mcp.engine.overlay_view import PRIMARY, render_overlay

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_artifacts")
os.makedirs(ART, exist_ok=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message)


def main() -> None:
    now = time.monotonic()
    events = [{
        "tool": "look", "label": "观察界面", "detail": "Notepad",
        "status": "success", "created": now - 1, "duration": 0.34,
    }, {
        "tool": "click", "label": "点击", "detail": "e4_3",
        "status": "running", "created": now, "duration": None,
    }]
    layers = {}
    for scale in (1.0, 1.5, 2.0):
        layer = render_overlay((1280, 800), events, now, scale)
        layers[scale] = layer
        background = Image.new("RGBA", layer.size, (16, 24, 39, 255))
        background.alpha_composite(layer)
        background.convert("RGB").save(
            os.path.join(ART, f"overlay_preview_{int(scale * 100)}.png"))
    light_background = Image.new("RGBA", layers[1.5].size, (245, 247, 250, 255))
    light_background.alpha_composite(layers[1.5])
    light_background.convert("RGB").save(os.path.join(ART, "overlay_preview_light.png"))
    check(layers[1.0].getpixel((640, 400))[3] == 0, "中心区域完全透明")
    check(layers[1.0].getpixel((640, 2))[:3] == PRIMARY, "主色与 irmia.me 一致")
    check(layers[1.0].getpixel((640, 100))[3] == 0
          and layers[2.0].getpixel((640, 100))[3] > 0,
          "渐变深度随 DPI 比例扩大")

    controller = overlay.AgentOverlay()
    foreground_before = desktop.foreground_window()
    try:
        event_id = controller.begin("look", {"window": "Notepad"})
        check(controller._frame_ready, "完整首帧完成后才显示窗口")
        time.sleep(0.8)
        controller.end(event_id, True, 0.35)
        time.sleep(0.3)
        status = controller.status()
        check(status["running"] and not status["error"], f"原生覆盖层启动: {status['error']}")
        check(status["capture_excluded"], "覆盖层被系统截图排除")
        regions = controller._display_regions()
        print("display_regions", regions)
        check(bool(regions) and all(0.75 <= scale <= 4.0 for _, scale in regions),
              "每个显示器使用独立 DPI 缩放")
        system_scale = desktop.coord_space_info((100, 100)).get("scale_factor") or 1.0
        check(max(scale for _, scale in regions) + 0.01 >= system_scale,
              f"显示器 DPI 覆盖系统缩放 {system_scale}")
        foreground_after = desktop.foreground_window()
        check((foreground_before or {}).get("hwnd") == (foreground_after or {}).get("hwnd"),
              "覆盖层不抢占前台焦点")
        shot = desktop.screenshot()
        shot.save(os.path.join(ART, "overlay_native_capture.png"))
        shot.close()

        import ctypes
        user32 = ctypes.windll.user32
        user32.SetWindowDisplayAffinity(controller._hwnd, 0)
        try:
            visible = desktop.screenshot()
            left, top, width, height = controller._bounds
            pixel = visible.getpixel((left + width // 2, top + 2))
            visible.save(os.path.join(ART, "overlay_visible_capture.png"))
            visible.close()
            check(pixel[2] > pixel[0], f"原生像素保持蓝色而不是红色: {pixel}")
        finally:
            user32.SetWindowDisplayAffinity(controller._hwnd, 0x11)
        print("last_frame_ms", controller.status().get("last_frame_ms"))

        inp.press(["esc"])
        time.sleep(0.2)
        check(not controller.is_paused(), "Agent 注入的 Esc 不会暂停")
        controller.pause()
        check(controller.is_paused(), "物理 Esc 分支进入暂停状态")
        controller.resume()
        time.sleep(0.2)
        check(not controller.is_paused() and controller.status()["visible"],
              "明确恢复后覆盖层重新显示")
    finally:
        controller.close()
    print("Overlay checks completed")


if __name__ == "__main__":
    main()
