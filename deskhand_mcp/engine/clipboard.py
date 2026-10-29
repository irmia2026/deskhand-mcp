"""Conservative full-format clipboard backup for the optional paste path."""

from __future__ import annotations

import time


class ClipboardBackup:
    def __init__(self) -> None:
        self._items: list[tuple[int, object]] = []
        self.formats_before: list[int] = []
        self.formats_after: list[int] = []
        self.restored = False

    @staticmethod
    def _read_all() -> list[tuple[int, object]]:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            items = []
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                value = win32clipboard.GetClipboardData(fmt)
                if not isinstance(value, (str, bytes, tuple, list)):
                    raise TypeError(
                        f"剪贴板格式 {fmt} 返回 {type(value).__name__}，无法安全复制句柄")
                items.append((fmt, value))
            return items
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def _formats() -> list[int]:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            formats = []
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                formats.append(fmt)
            return formats
        finally:
            win32clipboard.CloseClipboard()

    def __enter__(self):
        last_error = None
        for _ in range(3):
            try:
                self._items = self._read_all()
                self.formats_before = [fmt for fmt, _ in self._items]
                return self
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)
        raise RuntimeError(
            f"无法完整备份当前剪贴板，已在任何修改之前取消: {last_error}")

    def _restore(self) -> None:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            for fmt, value in self._items:
                win32clipboard.SetClipboardData(fmt, value)
        finally:
            win32clipboard.CloseClipboard()
        self.formats_after = self._formats()
        missing = set(self.formats_before) - set(self.formats_after)
        if missing:
            raise RuntimeError(f"剪贴板恢复后缺少格式: {sorted(missing)}")
        self.restored = True

    def __exit__(self, exc_type, exc, tb) -> None:
        last_error = None
        for _ in range(3):
            try:
                self._restore()
                return
            except Exception as restore_error:
                last_error = restore_error
                time.sleep(0.1)
        raise RuntimeError(f"剪贴板恢复失败: {last_error}")
