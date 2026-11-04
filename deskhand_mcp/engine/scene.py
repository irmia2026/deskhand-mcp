"""Scene snapshots, target-window context and stable pagination."""

from __future__ import annotations

import time
from typing import Optional

_TTL = 120.0

_snapshot: dict = {
    "ts": 0.0, "window": "", "hwnd": None, "app": "", "scope": "window",
    "generation": 0, "elements": [], "image": None, "origin": (0, 0),
}
_views: dict = {}
_view_seq = 0


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
    _views.clear()
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


def elements() -> list[dict]:
    return [] if _expired() else [dict(el) for el in _snapshot["elements"]]


def register_view(screen_rect, image_size, coordinate_space="image_pixels") -> dict:
    """Record an exact affine mapping, independent of display DPI and model resizing."""
    global _view_seq
    if _expired():
        raise ValueError("快照已过期，请重新 look")
    if coordinate_space not in ("image_pixels", "normalized_0_1000"):
        raise ValueError("未知图片坐标空间")
    _view_seq += 1
    image_id = f"g{current_generation()}v{_view_seq}"
    rect = [int(v) for v in screen_rect]
    width, height = (int(v) for v in image_size)
    if width <= 0 or height <= 0 or rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError("无效图像范围")
    scale_x = (rect[2]-rect[0]) / (1000 if coordinate_space == "normalized_0_1000" else width)
    scale_y = (rect[3]-rect[1]) / (1000 if coordinate_space == "normalized_0_1000" else height)
    mapping = {"image_id": image_id, "image_size": [width, height],
               "screen_rect": rect, "coordinate_space": coordinate_space,
               "scale_to_screen": [scale_x, scale_y],
               "origin_screen": rect[:2], "generation": current_generation()}
    _views[image_id] = mapping
    if len(_views) > 64:
        del _views[next(iter(_views))]
    return dict(mapping)


def view(image_id: str) -> dict:
    if _expired() or image_id not in _views:
        raise ValueError("stale：图像编号不存在或已过期，请重新 look/inspect_region")
    return dict(_views[image_id])


def image_point(image_id: str, x: float, y: float) -> tuple[int, int]:
    import math
    mapping = view(image_id)
    l,t,r,b = mapping["screen_rect"]
    if mapping.get("coordinate_space") == "normalized_0_1000":
        if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1000 or not 0 <= y <= 1000:
            raise ValueError("网格逻辑坐标越界；请使用 0–1000")
        return min(r-1, int(l+x*(r-l)/1000)), min(b-1, int(t+y*(b-t)/1000))
    width, height = mapping["image_size"]
    if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x < width or not 0 <= y < height:
        raise ValueError("图内坐标越界；请使用返回的 image_size 像素坐标")
    return min(r-1, int(l+x*(r-l)/width)), min(b-1, int(t+y*(b-t)/height))


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
    _views.clear()
    image = _snapshot.get("image")
    if image is not None:
        image.close()
    _snapshot.update({
        "ts": 0.0, "window": "", "hwnd": None, "app": "", "scope": "window",
        "elements": [], "image": None, "origin": (0, 0),
        "generation": int(_snapshot.get("generation", 0)) + 1,
    })
