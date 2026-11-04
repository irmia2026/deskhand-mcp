"""
desktop.py — 桌面基础层：单线程执行器、DPI 感知、窗口枚举、截图。

设计要点：
- 所有桌面操作（截图 / 鼠标 / 键盘 / OCR / UIA）都在同一个专用 STA 线程里串行执行：
  鼠标是全局共享资源，串行避免竞态，COM/UIA 不跨线程使用。
- SetProcessDPIAware 保证 GetWindowRect 的坐标与 ImageGrab 截图像素一致。
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


def _ensure_dpi_aware() -> None:
    """声明 DPI 感知（进程级，幂等）。可在任意线程调用。"""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _init_worker() -> None:
    """执行器线程初始化：DPI 感知 + STA COM 初始化（UIA L0 用）。

    CoInitializeEx(None, 0x2)：0x2=COINIT_APARTMENTTHREADED（STA）。
    0x0=MTA 会触发 RPC_E_CHANGED_MODE；uiautomation 未安装时此调用也无害。
    只在此线程做——不要在其它线程随手调（会改变该线程的未来套间模型）。
    """
    _ensure_dpi_aware()
    if sys.platform == "win32":
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        except Exception:
            pass


_EXEC = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="deskhand-desktop", initializer=_init_worker
)


async def run(fn, *args, **kwargs):
    """在桌面专用线程中执行同步函数。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXEC, lambda: fn(*args, **kwargs))


def shutdown() -> None:
    """关闭桌面执行器（插件 terminate 时调用）。热重载会重建模块与新执行器。"""
    _EXEC.shutdown(wait=False)


# ── 窗口枚举与匹配 ──────────────────────────────────────────────

_SYSTEM_WINDOW_TITLES = {
    "",
    "program manager",
    "default ime",
    "msctfime ui",
    "application frame host",
    "windows shell experience host",
    "search",
    "设置",
    "任务视图",
    "任务栏",
}

_WINDOW_ALIASES = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "qq": "腾讯qq",
    "wechat": "微信",
}


def _rect_on_screen(rect) -> bool:
    """矩形是否与虚拟屏有交集（过滤 -21333 这类完全在屏外的幽灵窗口）。"""
    left, top, right, bottom = screen_bounds()
    return not (rect[2] <= left or rect[0] >= right or rect[3] <= top or rect[1] >= bottom)


# UWP 应用窗口类：最小化后它会留下一个「IsWindowVisible=1 + rect 正常」的幽灵窗口，
# 而它实际不在屏幕上（2026-09 实测：计算器最小化后 CoreWindow 报 rect (0,1,1800,1391)，
# 但该区域实际显示的是别的窗口）——不过滤就会截到错误内容、污染整条定位链。
_UWP_CORE_CLASS = "Windows.UI.Core.CoreWindow"


def _self_displayed(hwnd: int, rect) -> bool:
    """窗口中心点的顶层窗口是不是它自己（否则就是幽灵）。

    只对 UWP CoreWindow 类使用：普通窗口被其他窗口遮挡是正常情形，
    不能因此从枚举里剔除（那会让 find_window 找不到被遮住的窗口）。
    """
    import win32con
    import win32gui

    try:
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        top = win32gui.GetAncestor(
            win32gui.WindowFromPoint((cx, cy)), win32con.GA_ROOT
        )
        return top == hwnd
    except Exception:
        return True  # 判定不了就不剔除（宁可多报，不可漏报）


def enum_windows() -> list[dict]:
    """枚举可见顶层窗口：[{hwnd, title, rect, class_name, iconic}]，z-order 从顶到底。

    过滤：不可见、系统窗、极小窗、矩形完全在虚拟屏之外的幽灵窗口。
    最小化窗口保留（rect=None）——否则 min 之后 restore 会匹配不到原窗口。
    """
    import win32gui

    found: list[dict] = []

    def _cb(hwnd: int, _) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title or title.lower() in _SYSTEM_WINDOW_TITLES:
                return
            # 最小化窗口的 GetWindowRect 返回无效/极小矩形（如 158×26），
            # 不能被尺寸过滤器误杀——否则 min 之后 restore 会匹配不到原窗口
            if win32gui.IsIconic(hwnd):
                found.append(
                    {
                        "hwnd": hwnd,
                        "title": title,
                        "rect": None,
                        "class_name": win32gui.GetClassName(hwnd) or "",
                        "iconic": True,
                    }
                )
                return
            rect = win32gui.GetWindowRect(hwnd)
            if rect[2] - rect[0] < 80 or rect[3] - rect[1] < 60:
                return
            if not _rect_on_screen(rect):
                return  # 幽灵窗口（坐标完全在屏外，如 -21333,-21333）
            cls = win32gui.GetClassName(hwnd) or ""
            if cls == _UWP_CORE_CLASS and not _self_displayed(hwnd, rect):
                return  # 最小化 UWP 留下的幽灵 CoreWindow（rect 正常但不是它在屏幕上）
            found.append(
                {
                    "hwnd": hwnd,
                    "title": title,
                    "rect": tuple(rect),
                    "class_name": cls,
                    "iconic": False,
                }
            )
        except Exception:
            return

    win32gui.EnumWindows(_cb, None)
    return found


# ── 窗口句柄记忆：min/max 过的窗口，restore/focus 时直接用句柄，不重新匹配标题 ──
#
# hwnd 会被 OS 复用：窗口销毁后同一值可能分给无关新窗口。
# 所以记忆同时存 class_name 快照，recall 时复核，防止对错误窗口执行 close/min。

_window_memory: dict[str, dict] = {}


def remember_window(keyword: str, hwnd: int, class_name: str = "") -> None:
    kw = (keyword or "").strip().lower()
    if kw:
        _window_memory[kw] = {"hwnd": hwnd, "class_name": class_name or ""}


def recall_window(keyword: str) -> Optional[int]:
    """按上次匹配时的关键词回忆 hwnd（校验句柄有效 + 类名一致，防 hwnd 复用）。"""
    import win32gui

    kw = (keyword or "").strip().lower()
    rec = _window_memory.get(kw)
    if not rec:
        return None
    hwnd = rec["hwnd"]
    try:
        if not win32gui.IsWindow(hwnd):
            raise ValueError("gone")
        if rec["class_name"] and win32gui.GetClassName(hwnd) != rec["class_name"]:
            raise ValueError("class changed")
        return hwnd
    except Exception:
        _window_memory.pop(kw, None)
        return None


# 关键词 → 进程名（标题档全军覆没时按进程找窗口。
# QQ NT 这类标题=会话名的应用，标题匹配先天找不到，只有按进程找才治本）
_PROCESS_ALIASES = {
    "qq": "qq.exe",
    "wechat": "wechat.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
}


def _usable(w: Optional[dict]) -> bool:
    """候选窗口当前可用（有有效 rect、不是最小化/幽灵态）。"""
    return bool(w and valid_rect(w.get("rect")))


def _process_fallback(kw: str) -> Optional[dict]:
    """按进程名找窗口：非最小化 + 屏内 + 面积最大。"""
    exe = _PROCESS_ALIASES.get(kw)
    if not exe:
        return None
    cands = [
        w for w in enum_windows()
        if _usable(w) and app_key(w) == exe
    ]
    if not cands:
        return None
    if len(cands)>1:
        raise ValueError(f"应用有多个可用窗口，请 list_windows 后指定 hwnd：{[(w['hwnd'],w['title']) for w in cands]}")
    return cands[0]


def find_window(keyword: str, include_iconic: bool = False) -> Optional[dict]:
    """按标题关键词匹配窗口：别名展开 → 精确 → 包含 → 进程名兜底。

    无效 rect（最小化/幽灵态）的候选一律跳过并继续往下一档找——
    不会再让「标题恰好叫 QQ 的最小化幽灵窗口」抢走匹配。
    include_iconic=True 时不过滤最小化窗口（window_action 的 restore 需要）。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return None
    kws = [kw]
    expanded = _WINDOW_ALIASES.get(kw)
    if expanded:
        kws.append(expanded)

    wins = enum_windows()

    def _ok(w: dict) -> bool:
        return True if include_iconic else _usable(w)

    exact=[w for w in wins if w["title"].lower().strip() in kws and _ok(w)]
    contained=[w for w in wins if any(k in w["title"].lower() for k in kws) and _ok(w)]
    matches=exact or contained
    if len(matches)>1:
        raise ValueError(f"窗口标题不唯一，请 list_windows 后指定 hwnd：{[(w['hwnd'],w['title']) for w in matches]}")
    if matches:
        return matches[0]
    return _process_fallback(kw)


def foreground_window() -> Optional[dict]:
    """当前前台窗口（含 hwnd/title/rect/class_name）。"""
    import win32gui

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        return {
            "hwnd": hwnd,
            "title": (win32gui.GetWindowText(hwnd) or "").strip(),
            "rect": tuple(win32gui.GetWindowRect(hwnd)),
            "class_name": win32gui.GetClassName(hwnd) or "",
        }
    except Exception:
        return None


def window_rect(hwnd: int) -> Optional[tuple]:
    import win32gui

    try:
        return tuple(win32gui.GetWindowRect(hwnd))
    except Exception:
        return None


def window_from_hwnd(hwnd) -> Optional[dict]:
    import win32gui

    try:
        hwnd = int(hwnd)
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        iconic = bool(win32gui.IsIconic(hwnd))
        rect = None if iconic else tuple(win32gui.GetWindowRect(hwnd))
        client = win32gui.GetClientRect(hwnd)
        cl,ct=win32gui.ClientToScreen(hwnd,(client[0],client[1]))
        cr,cb=win32gui.ClientToScreen(hwnd,(client[2],client[3]))
        return {
            "hwnd": hwnd,
            "title": (win32gui.GetWindowText(hwnd) or "").strip(),
            "rect": rect,
            "class_name": win32gui.GetClassName(hwnd) or "",
            "iconic": iconic,
            "client_rect": None if iconic else (cl,ct,cr,cb),
        }
    except Exception:
        return None


def window_pid(hwnd) -> Optional[int]:
    import win32process
    try:
        return int(win32process.GetWindowThreadProcessId(int(hwnd))[1])
    except Exception:
        return None


def valid_rect(rect) -> bool:
    """矩形有效且不是最小化窗口（最小化时 rect 约为 -32000）。"""
    if not rect or len(rect) != 4:
        return False
    left, top, right, bottom = rect
    return right > left and bottom > top and left > -30000 and top > -30000


def app_key(win: dict) -> str:
    """窗口所属应用的稳定标识（记忆库键）：优先 exe 文件名，兜底 class_name。

    用 class_name 会在 Chrome_WidgetWin_1 系（浏览器/QQ/各种 Electron）之间撞车。
    """
    import os

    try:
        import win32api
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(win["hwnd"])
        h = win32api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        try:
            exe = win32process.GetModuleFileNameEx(h, 0)
        finally:
            win32api.CloseHandle(h)
        name = os.path.basename(exe).strip().lower()
        if name:
            return name
    except Exception:
        pass
    return (win.get("class_name") or "unknown").lower()


# ── 截图 ────────────────────────────────────────────────────────

def virtual_screen_origin() -> tuple[int, int]:
    """虚拟屏原点（多显示器时可能为负值）。全屏截图的 (0,0) 对应该原点。"""
    try:
        x = ctypes.windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        y = ctypes.windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        return int(x), int(y)
    except Exception:
        return (0, 0)


def screen_bounds() -> tuple[int, int, int, int]:
    """虚拟屏边界 (left, top, right, bottom)，用于坐标钳制。"""
    try:
        x = ctypes.windll.user32.GetSystemMetrics(76)
        y = ctypes.windll.user32.GetSystemMetrics(77)
        w = ctypes.windll.user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        h = ctypes.windll.user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if w > 0 and h > 0:
            return (int(x), int(y), int(x + w), int(y + h))
    except Exception:
        pass
    return (0, 0, 1 << 15, 1 << 15)

def screenshot(bbox: Optional[tuple] = None):
    """截屏（全虚拟屏或指定区域），必要时临时隐藏 Agent 覆盖层。"""
    from PIL import ImageGrab

    from . import overlay

    with overlay.capture_guard():
        return ImageGrab.grab(bbox=bbox, all_screens=True)


def screenshot_window(hwnd: int):
    """截取指定窗口区域；窗口无效时退化为全屏。"""
    rect = window_rect(hwnd)
    if valid_rect(rect):
        return screenshot(rect)
    return screenshot()


def cursor_pos() -> tuple[int, int]:
    import win32api

    return tuple(win32api.GetCursorPos())


def same_window_family(target_hwnd: int, other_hwnd: int) -> bool:
    """Include child controls, owned dialogs and popup menus, never unrelated apps."""
    import win32gui
    def owner_root(hwnd):
        seen = set()
        while hwnd and hwnd not in seen:
            seen.add(hwnd)
            root = win32gui.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
            owner = win32gui.GetWindow(root, 4)  # GW_OWNER
            if not owner:
                return root
            hwnd = owner
        return hwnd
    try:
        return bool(target_hwnd and other_hwnd and owner_root(int(target_hwnd)) == owner_root(int(other_hwnd)))
    except Exception:
        return False


def _validate_image_points(image_id: str, points) -> None:
    from . import scene, verify
    from ..rw import RwError

    try:
        mapping = scene.view(image_id)
    except ValueError as exc:
        raise RwError(f"stale：{exc}") from exc
    shot = scene.current_image()
    if shot is None:
        raise RwError("stale：图片所属快照已失效，请重新 look")
    ox, oy = scene.current_origin()
    observed = (ox, oy, ox + shot.width, oy + shot.height)
    left, top, right, bottom = mapping["screen_rect"]
    if not (observed[0] <= left < right <= observed[2]
            and observed[1] <= top < bottom <= observed[3]):
        raise RwError("stale：图片范围不在当前快照内，请重新 look")
    live = screenshot(observed)
    try:
        for point in points:
            cx, cy = int(point[0]) - ox, int(point[1]) - oy
            box = (max(0, cx - 16), max(0, cy - 16),
                   min(shot.width, cx + 16), min(shot.height, cy + 16))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise RwError("stale：图片落点不在快照范围内，请重新 look")
            before = shot.crop(box)
            after = live.crop(box)
            try:
                diff = verify.diff_images(before, after)
            finally:
                before.close()
                after.close()
            if diff["changed"] and (diff["percent"] is None or diff["percent"] >= 20):
                raise RwError("stale：图片落点周围的界面已变化，请重新 look 后使用新 image_id")
    finally:
        live.close()


def validate_input_target(win, points, image_id: str = "") -> None:
    """Fail closed if focus, ownership or an image-derived location is stale.

    Called on the input worker immediately before dispatch. Full-screen operation
    is allowed only when the caller explicitly resolved a screen context.
    """
    import win32gui
    from ..rw import RwError
    if win is not None:
        fresh = window_from_hwnd(win["hwnd"])
        if fresh is None:
            raise RwError("stale：目标窗口已关闭，未派发输入")
        foreground = win32gui.GetForegroundWindow()
        if not same_window_family(fresh["hwnd"], foreground):
            raise RwError("目标窗口失去焦点，未派发输入；请重新 look")
        if points and tuple(fresh["rect"]) != tuple(win["rect"]):
            raise RwError("stale：窗口位置或大小已改变，请重新 look 后使用新坐标")
        if points:
            from . import scene
            shot = scene.current_image()
            if shot is not None and scene.current_hwnd() == fresh["hwnd"]:
                ox,oy = scene.current_origin()
                observed = (ox,oy,ox+shot.width,oy+shot.height)
                if observed != tuple(fresh["rect"]):
                    raise RwError("stale：窗口相对截图已移动或缩放，请重新 look")
    bounds = screen_bounds()
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise RwError("路径点必须是 [x,y]")
        x, y = int(point[0]), int(point[1])
        if not bounds[0] <= x < bounds[2] or not bounds[1] <= y < bounds[3]:
            raise RwError(f"坐标 ({x},{y}) 在虚拟屏幕之外，未派发输入")
        hit = win32gui.WindowFromPoint((x, y))
        if win is not None and not same_window_family(win["hwnd"], hit):
            raise RwError(f"坐标 ({x},{y}) 不属于目标窗口或其对话框，未派发输入；请重新 look")
    if image_id and points:
        _validate_image_points(image_id, points)


def coord_space_info(size: tuple[int, int]) -> dict:
    """返回坐标空间标注：物理像素（截图/click 使用的坐标系）与系统逻辑像素。

    进程是 DPI 感知的，截图和 win32 坐标都是物理像素；逻辑尺寸 = 物理 / (DPI/96)。
    """
    _ensure_dpi_aware()  # 幂等：确保 DPI 感知已设置，GetDpiForSystem 才返回真实 DPI
                         # （只做 DPI，不做 COM 初始化——本函数可能在调用方线程执行）
    phys_w, phys_h = int(size[0]), int(size[1])
    info = {"coordinate_space": "physical_pixels", "physical_size": [phys_w, phys_h]}
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / 96.0
        info["scale_factor"] = round(scale, 3)
        info["logical_size"] = [round(phys_w / scale), round(phys_h / scale)]
    except Exception:
        info["scale_factor"] = None
        info["logical_size"] = None
    return info


def operation_context(hwnd=None) -> dict:
    """返回可安全获取的权限和前台上下文，不猜测目标进程完整性级别。"""
    foreground = foreground_window()
    target = window_from_hwnd(hwnd) if hwnd is not None else None
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        elevated = None
    return {
        "server_elevated": elevated,
        "foreground": foreground,
        "target": target,
        "note": "UAC 安全桌面不可穿透；目标是否提权需结合原始 Win32 错误判断",
    }


def sleep(seconds: float) -> None:
    time.sleep(seconds)
