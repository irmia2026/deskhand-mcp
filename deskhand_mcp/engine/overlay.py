"""Native, click-through agent overlay with physical-Escape pause."""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes

from .overlay_view import event_text, premultiplied_bgra, render_overlay

logger = logging.getLogger("deskhand.overlay")

_WM_COMMAND = 0x8000 + 41
_WM_TIMER = 0x0113
_WM_DISPLAYCHANGE = 0x007E
_WM_QUIT = 0x0012
_WH_KEYBOARD_LL = 13
_LLKHF_INJECTED = 0x10
_ULW_ALPHA = 0x02


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class AgentOverlay:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._commands: queue.Queue = queue.Queue()
        self._events: list[dict] = []
        self._thread = None
        self._ready = threading.Event()
        self._hwnd = None
        self._desired_visible = False
        self._capture_hold = 0
        self._capture_excluded = False
        self._paused = False
        self._closed = False
        self._dirty = True
        self._error = ""
        self._seq = 0
        self._bounds = (0, 0, 1, 1)
        self._class_name = ""
        self._hook = None
        self._hook_callback = None
        self._last_render = 0.0
        self._last_frame_ms = 0.0
        self._screen_dc = None
        self._frame_dc = None
        self._frame_dib = None
        self._frame_bits = None
        self._frame_old = None
        self._frame_size = None
        self._frame_ready = False

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._thread is not None:
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, name="deskhand-overlay", daemon=True)
            self._thread.start()
        self._ready.wait(4)

    def begin(self, tool: str, args: dict) -> int:
        with self._lock:
            self._seq += 1
            event_id = self._seq
            label, detail = event_text(tool, args)
            self._events.append({
                "id": event_id, "tool": tool, "label": label, "detail": detail,
                "status": "running", "created": time.monotonic(), "duration": None,
            })
            self._events = self._events[-12:]
            if not self._paused:
                self._desired_visible = True
        self._start()
        self._commands.put(("show", None))
        return event_id

    def end(self, event_id: int, ok: bool, duration: float) -> None:
        with self._lock:
            for event in self._events:
                if event["id"] == event_id:
                    event["status"] = "success" if ok else "failed"
                    event["duration"] = duration
                    break
        self._commands.put(("topmost", None))
        self._commands.put(("refresh", None))

    def pause(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._paused = True
            self._desired_visible = False
        self._commands.put(("hide", None))

    def resume(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._paused = False
            self._desired_visible = True
        self._start()
        self._commands.put(("show", None))

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def should_cancel(self) -> bool:
        return self.is_paused()

    def status(self) -> dict:
        with self._lock:
            return {
                "ok": True, "paused": self._paused, "visible": self._desired_visible,
                "running": self._thread is not None and self._thread.is_alive(),
                "capture_excluded": self._capture_excluded,
                "last_frame_ms": self._last_frame_ms,
                "error": self._error or None,
                "recent_events": [dict(event) for event in self._events[-4:]],
            }

    def _post(self, message: int = _WM_COMMAND) -> None:
        if self._hwnd:
            import win32gui

            try:
                win32gui.PostMessage(self._hwnd, message, 0, 0)
            except Exception:
                pass

    def _set_visible(self, visible: bool) -> None:
        if not self._hwnd:
            return
        import win32con
        import win32gui

        self._update_bounds()
        if visible and not self._frame_ready:
            self._dirty = True
            return
        win32gui.ShowWindow(
            self._hwnd, win32con.SW_SHOWNOACTIVATE if visible else win32con.SW_HIDE)
        self._dirty = True

    def _update_bounds(self) -> None:
        import win32api
        import win32con
        import win32gui

        left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        bounds = (left, top, width, height)
        if bounds != self._bounds:
            self._bounds = bounds
            win32gui.SetWindowPos(
                self._hwnd, win32con.HWND_TOPMOST, left, top, width, height,
                win32con.SWP_NOACTIVATE)
            self._dirty = True

    def _drain(self) -> None:
        while True:
            try:
                command, ack = self._commands.get_nowait()
            except queue.Empty:
                break
            if command == "topmost":
                import win32con
                import win32gui
                if self._hwnd:
                    win32gui.SetWindowPos(
                        self._hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            elif command == "resume":
                self._set_visible(self._desired_visible and not self._capture_hold)
            elif command in ("show", "hide", "refresh"):
                self._set_visible(self._desired_visible and not self._capture_hold)
                self._dirty = True
            elif command == "capture":
                self._set_visible(self._desired_visible and not self._capture_hold)
            elif command == "quit":
                import win32gui

                win32gui.PostQuitMessage(0)
            if ack is not None:
                ack.set()

    def _release_frame_resources(self) -> None:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        if self._frame_old and self._frame_dc:
            gdi32.SelectObject(self._frame_dc, self._frame_old)
        if self._frame_dib:
            gdi32.DeleteObject(self._frame_dib)
        if self._frame_dc:
            gdi32.DeleteDC(self._frame_dc)
        if self._screen_dc:
            user32.ReleaseDC(0, self._screen_dc)
        self._screen_dc = self._frame_dc = self._frame_dib = None
        self._frame_bits = self._frame_old = self._frame_size = None

    def _ensure_frame_resources(self, width: int, height: int) -> None:
        if self._frame_size == (width, height) and self._frame_dib:
            return
        self._release_frame_resources()
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateDIBSection.restype = ctypes.c_void_p
        gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        user32.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE), ctypes.c_void_p, ctypes.POINTER(_POINT),
            wintypes.DWORD, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD]
        self._screen_dc = user32.GetDC(0)
        self._frame_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        self._frame_dib = gdi32.CreateDIBSection(
            self._screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not self._frame_dib or not bits.value:
            self._release_frame_resources()
            raise RuntimeError("CreateDIBSection failed")
        self._frame_bits = bits
        self._frame_old = gdi32.SelectObject(self._frame_dc, self._frame_dib)
        self._frame_size = (width, height)

    def _display_regions(self) -> list[tuple[tuple, float]]:
        left, top, width, height = self._bounds
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            system_dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            system_dpi = 96
        try:
            import win32api

            monitors = win32api.EnumDisplayMonitors()
            if not monitors:
                raise RuntimeError("no monitors")
            shcore = ctypes.windll.shcore
            shcore.GetDpiForMonitor.argtypes = [
                ctypes.c_void_p, ctypes.c_int,
                ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
            regions = []
            for handle, _, rect in monitors:
                dpi = 96
                try:
                    dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
                    if shcore.GetDpiForMonitor(handle, 0, ctypes.byref(dpi_x),
                                               ctypes.byref(dpi_y)) == 0:
                        dpi = dpi_x.value
                except Exception:
                    pass
                if dpi == 96 and system_dpi > 96:
                    dpi = system_dpi
                regions.append((tuple(rect), max(0.75, min(dpi / 96, 4.0))))
            return regions
        except Exception:
            return [((left, top, left + width, top + height), system_dpi / 96)]

    def _render(self) -> None:
        if not self._hwnd or not self._desired_visible or self._capture_hold:
            return
        now = time.monotonic()
        animating = any(now - event["created"] < 0.3 for event in self._events)
        running = any(event["status"] == "running" for event in self._events)
        if not self._dirty and not animating and not (running and now - self._last_render > 1):
            return
        from PIL import Image

        with self._lock:
            events = [dict(event) for event in self._events]
        left, top, width, height = self._bounds
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for rect, scale in self._display_regions():
            region = render_overlay(
                (rect[2] - rect[0], rect[3] - rect[1]), events, now, scale)
            image.alpha_composite(region, (rect[0] - left, rect[1] - top))
        pixels = premultiplied_bgra(image)

        self._ensure_frame_resources(width, height)
        ctypes.memmove(self._frame_bits, pixels, len(pixels))
        point_dst = _POINT(left, top)
        point_src = _POINT(0, 0)
        size = _SIZE(width, height)
        blend = _BLENDFUNCTION(0, 0, 255, 1)
        result = ctypes.windll.user32.UpdateLayeredWindow(
            self._hwnd, self._screen_dc, ctypes.byref(point_dst), ctypes.byref(size),
            self._frame_dc, ctypes.byref(point_src), 0, ctypes.byref(blend), _ULW_ALPHA)
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        self._dirty = False
        self._last_render = now
        self._last_frame_ms = round((time.monotonic() - now) * 1000, 2)
        self._frame_ready = True
        if self._desired_visible and not self._capture_hold:
            import win32con
            import win32gui
            win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)

    def _install_hook(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, callback_type, ctypes.c_void_p, wintypes.DWORD]

        def keyboard_hook(code, wparam, lparam):
            if code >= 0:
                data = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if (data.vkCode == 0x1B and not data.flags & _LLKHF_INJECTED
                        and self._desired_visible and not self._paused):
                    self.pause()
                    self._post()
                    return 1
            return user32.CallNextHookEx(self._hook, code, wparam, lparam)

        self._hook_callback = callback_type(keyboard_hook)
        self._hook = user32.SetWindowsHookExW(
            _WH_KEYBOARD_LL, self._hook_callback,
            kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.GetLastError())

    def _run(self) -> None:
        try:
            import win32api
            import win32con
            import win32gui

            self._class_name = f"DeskhandOverlay_{id(self):x}"
            instance = win32api.GetModuleHandle()

            def wnd_proc(hwnd, msg, wparam, lparam):
                if msg in (_WM_COMMAND, _WM_TIMER, _WM_DISPLAYCHANGE):
                    self._drain()
                    if msg == _WM_DISPLAYCHANGE:
                        self._update_bounds()
                    try:
                        self._render()
                    except Exception as exc:
                        self._error = str(exc)
                        logger.warning("覆盖层渲染失败: %s", exc)
                        self._desired_visible = False
                        self._set_visible(False)
                    return 0
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = wnd_proc
            wc.lpszClassName = self._class_name
            wc.hInstance = instance
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            win32gui.RegisterClass(wc)
            ex_style = (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT |
                        win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW |
                        win32con.WS_EX_NOACTIVATE)
            self._hwnd = win32gui.CreateWindowEx(
                ex_style, self._class_name, "", win32con.WS_POPUP,
                0, 0, 1, 1, 0, 0, instance, None)
            self._update_bounds()
            ctypes.windll.user32.SetWindowDisplayAffinity.argtypes = [
                ctypes.c_void_p, wintypes.DWORD]
            self._capture_excluded = bool(
                ctypes.windll.user32.SetWindowDisplayAffinity(self._hwnd, 0x11))
            ctypes.windll.user32.SetTimer.argtypes = [
                ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
            ctypes.windll.user32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            ctypes.windll.user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            self._install_hook()
            ctypes.windll.user32.SetTimer(self._hwnd, 1, 33, None)
            self._drain()
            self._render()
            self._ready.set()
            win32gui.PumpMessages()
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            logger.warning("覆盖层初始化失败: %s", exc)
            self._ready.set()
        finally:
            try:
                self._release_frame_resources()
                if self._hook:
                    ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
                    self._hook = None
                if self._hwnd:
                    import win32gui

                    ctypes.windll.user32.KillTimer(self._hwnd, 1)
                    win32gui.DestroyWindow(self._hwnd)
                if self._class_name:
                    import win32gui

                    win32gui.UnregisterClass(self._class_name, None)
            except Exception:
                pass

    @contextmanager
    def capture_guard(self):
        with self._lock:
            active = (self._thread is not None and self._thread.is_alive()
                      and self._desired_visible and not self._capture_excluded)
            if active:
                self._capture_hold += 1
        if not active:
            yield
            return
        ack = threading.Event()
        self._commands.put(("capture", ack))
        ack.wait(1)
        try:
            yield
        finally:
            with self._lock:
                self._capture_hold = max(0, self._capture_hold - 1)
            self._commands.put(("capture", None))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._desired_visible = False
        self._commands.put(("quit", None))
        self._post(_WM_QUIT)
        if self._thread is not None:
            self._thread.join(3)


_instance = None


def get_overlay():
    global _instance
    if _instance is None:
        from ..overlay_client import OverlayClient
        _instance = OverlayClient()
    return _instance


def capture_guard():
    return get_overlay().capture_guard()
