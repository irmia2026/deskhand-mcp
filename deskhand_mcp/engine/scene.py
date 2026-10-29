"""Scene snapshots, target-window context and stable pagination."""

from __future__ import annotations

import time
from typing import Optional

_TTL = 120.0

_snapshot: dict = {
    "ts": 0.0, "window": "", "hwnd": None, "app": "", "scope": "window",
    "generation": 0, "elements": [], "image": None, "origin": (0, 0),
}


def _expired() -> bool:
    return time.monotonic() - _snapshot["ts"] > _TTL


def register(elements: list[dict], window_title: str = "",
             shot=None, origin: tuple = (0, 0), window_hwnd=None,
             app: str = "", scope: str = "window") -> list[dict]:
    from . import memory as mem

    old_image = _snapshot.get("image")
    if old_image is not None:
        old_image.close()
    generation = int(_snapshot.get("generation", 0)) + 1
    numbered = []
    for i, el in enumerate(elements):
        item = dict(el)
        item["id"] = f"e{generation}_{i + 1}"
        if shot is not None:
            try:
                item["crop_sig"] = mem.crop_signature(
                    shot, int(el["x"] - origin[0]), int(el["y"] - origin[1])
                )
            except Exception:
                item["crop_sig"] = ""
        numbered.append(item)
    _snapshot.update({
        "ts": time.monotonic(), "window": window_title or "",
        "hwnd": window_hwnd, "app": app, "scope": scope,
        "generation": generation, "elements": numbered,
        "image": shot.copy() if shot is not None else None,
        "origin": tuple(origin),
    })
    return numbered


def resolve(element_id: str) -> Optional[dict]:
    if not element_id or _expired():
        return None
    eid = str(element_id).strip().lower()
    for el in _snapshot["elements"]:
        if el.get("id", "").lower() == eid:
            return el
    return None


def age() -> float:
    if not _snapshot["ts"]:
        return float("inf")
    return time.monotonic() - _snapshot["ts"]


def current_window() -> str:
    return "" if _expired() else _snapshot["window"]


def current_hwnd():
    return None if _expired() else _snapshot.get("hwnd")


def current_image():
    return None if _expired() else _snapshot.get("image")


def current_origin() -> tuple:
    return tuple(_snapshot.get("origin", (0, 0)))


def current_generation() -> int:
    return 0 if _expired() else int(_snapshot.get("generation", 0))


def current_context() -> dict:
    if _expired():
        return {}
    return {
        "hwnd": _snapshot.get("hwnd"), "title": _snapshot.get("window"),
        "app": _snapshot.get("app"), "scope": _snapshot.get("scope"),
        "generation": current_generation(),
    }


def matches_window(hwnd) -> bool:
    expected = current_hwnd()
    return expected is not None and int(expected) == int(hwnd)


def page(limit: int, cursor: str = "", source: str = "all", kind: str = "all") -> dict:
    if _expired():
        return {"ok": False, "stale": True, "error": "快照已过期，请重新 look"}
    generation = current_generation()
    offset = 0
    if cursor:
        try:
            cursor_generation, raw_offset = cursor.split(":", 1)
            if int(cursor_generation) != generation:
                return {"ok": False, "stale": True, "error": "翻页游标属于旧快照，请重新 look"}
            offset = int(raw_offset)
        except Exception:
            return {"ok": False, "error": "cursor 格式应为 generation:offset"}
    if offset < 0:
        return {"ok": False, "error": "cursor offset 不能为负数"}
    if source not in ("all", "uia", "ocr", "cv"):
        return {"ok": False, "error": "source 必须是 all/uia/ocr/cv"}
    if kind not in ("all", "actions", "text"):
        return {"ok": False, "error": "kind 必须是 all/actions/text"}
    limit = max(1, min(int(limit), 200))
    elements = list(_snapshot["elements"])
    if source != "all":
        elements = [el for el in elements if el.get("source") == source]
    if kind == "actions":
        elements = [el for el in elements if el.get("source") in ("uia", "cv", "memory")]
    elif kind == "text":
        elements = [el for el in elements
                    if el.get("type") in ("text", "input") or el.get("source") == "ocr"]
    total = len(elements)
    if offset > total:
        return {"ok": False, "error": f"cursor offset {offset} 超过元素总数 {total}"}
    shown = elements[offset:offset + limit]
    next_offset = offset + len(shown)
    return {
        "ok": True, "elements": shown, "offset": offset, "limit": limit,
        "total": total, "next_cursor": (
            f"{generation}:{next_offset}" if next_offset < total else None),
    }


def clear() -> None:
    image = _snapshot.get("image")
    if image is not None:
        image.close()
    _snapshot.update({
        "ts": 0.0, "window": "", "hwnd": None, "app": "", "scope": "window",
        "elements": [], "image": None, "origin": (0, 0),
        "generation": int(_snapshot.get("generation", 0)) + 1,
    })
