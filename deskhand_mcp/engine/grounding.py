"""Deterministic grounding and readable candidate crops for weaker vision hosts.

No model calls: names come from UIA/OCR; color/shape are explicit low-level
visual hints, never claims about the semantic meaning of an unnamed icon.
"""

from __future__ import annotations

import colorsys
import difflib
import re
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = re.sub(r"\(&?[a-z]\)$", "", text)
    return re.sub(r"[\s&…._()（）]+", "", text)


def describe(image, el, origin):
    """Cheap robust center-color/contour hints, optional OpenCV, no semantic guesses."""
    rect = [
        int(el.get(k, el["x"] if k in ("left", "right") else el["y"]))
        for k in ("left", "top", "right", "bottom")
    ]
    l, t, r, b = rect
    if r <= l or b <= t:
        return {}
    ox, oy = origin
    crop = image.crop(
        (
            max(0, l - ox),
            max(0, t - oy),
            min(image.width, r - ox),
            min(image.height, b - oy),
        )
    ).convert("RGB")
    if not crop.width or not crop.height:
        return {}
    small = crop.resize((24, 24))
    colors = []
    for red, green, blue in small.getdata():
        h, s, v = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if s > 0.35 and v > 0.25:
            colors.append((h, s, v))
    hint = {}
    if colors:
        # Hue bins avoid the discontinuity at red's 0/1 boundary.
        from collections import Counter

        def name(h):
            if h < 0.045 or h >= 0.95:
                return "red"
            if h < 0.12:
                return "orange"
            if h < 0.19:
                return "yellow"
            if h < 0.46:
                return "green"
            if h < 0.55:
                return "cyan"
            if h < 0.73:
                return "blue"
            return "purple"

        hint["color"] = Counter(name(c[0]) for c in colors).most_common(1)[0][0]
    if el.get("source") == "cv":
        try:
            import cv2
            import numpy as np

            gray = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 40, 120)
            contours, _ = cv2.findContours(
                edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if contours:
                contour = max(contours, key=cv2.contourArea)
                perimeter = cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
                circularity = (
                    4
                    * 3.14159265
                    * cv2.contourArea(contour)
                    / max(1, perimeter * perimeter)
                )
                if len(approx) == 3:
                    hint["shape"] = "triangle"
                elif len(approx) == 4:
                    # Distinguish rotated squares from axis-aligned rectangles.
                    p = approx.reshape(-1, 2)
                    dx, dy = p[1] - p[0]
                    hint["shape"] = (
                        "diamond"
                        if min(abs(int(dx)), abs(int(dy)))
                        > 0.2 * max(abs(int(dx)), abs(int(dy)))
                        else "rectangle"
                    )
                elif circularity > 0.75:
                    hint["shape"] = "circle"
        except ImportError:
            pass
    crop.close()
    small.close()
    return hint


ALIASES = {
    "红": "red",
    "红色": "red",
    "绿": "green",
    "绿色": "green",
    "蓝": "blue",
    "蓝色": "blue",
    "黄": "yellow",
    "黄色": "yellow",
    "紫": "purple",
    "紫色": "purple",
    "青色": "cyan",
    "橙色": "orange",
    "圆": "circle",
    "圆形": "circle",
    "矩形": "rectangle",
    "方形": "rectangle",
    "菱形": "diamond",
    "三角形": "triangle",
}


def search(
    elements,
    query="",
    position="",
    color="",
    shape="",
    source="all",
    limit=8,
    size=(1, 1),
    origin=(0, 0),
    control_type="",
):
    q = normalize(query)
    color = ALIASES.get(color, color).casefold()
    shape = ALIASES.get(shape, shape).casefold()
    result = []
    for el in elements:
        if source != "all" and el.get("source") != source:
            continue
        if control_type and el.get("type") != control_type:
            continue
        hint = el.get("visual_hint", {})
        if color and hint.get("color") != color:
            continue
        if shape and hint.get("shape") != shape:
            continue
        col = min(2, max(0, int((el["x"] - origin[0]) * 3 / max(1, size[0]))))
        row = min(2, max(0, int((el["y"] - origin[1]) * 3 / max(1, size[1]))))
        cell = f"{'ABC'[col]}{row + 1}"
        if position and cell != position.upper():
            continue
        names = [
            normalize(el.get("name", "")),
            normalize(el.get("ocr_text", "")),
            normalize(el.get("automation_id", "")),
        ]
        if not q:
            score, method = 1.0, "filters"
        elif q in names:
            score, method = 1.0, "exact"
        elif any(q in n for n in names if n):
            score, method = 0.85, "contains"
        else:
            score = max(
                (difflib.SequenceMatcher(None, q, n).ratio() for n in names if n),
                default=0,
            )
            method = "fuzzy"
            if score < 0.55:
                continue
        result.append(
            {
                **el,
                "match_score": round(score, 3),
                "match_type": method,
                "cell": cell,
                "click_args": {"element": el["id"]},
            }
        )
    if any(e["match_type"] == "exact" for e in result):
        result = [e for e in result if e["match_type"] == "exact"]
    elif any(e["match_type"] == "contains" for e in result):
        result = [e for e in result if e["match_type"] == "contains"]
    result.sort(
        key=lambda e: (
            -e["match_score"],
            {"uia": 0, "ocr": 1, "cv": 2}.get(e.get("source"), 3),
            e["y"],
            e["x"],
        )
    )
    # Same target found through more than one channel should not cause ambiguity.
    unique = []
    for candidate in result:
        if any(
            abs(candidate["x"] - prev["x"]) < 8 and abs(candidate["y"] - prev["y"]) < 8
            for prev in unique
        ):
            continue
        unique.append(candidate)
    return unique[: max(1, min(int(limit), 24))], len(unique)


def contact_sheet(image, candidates, origin):
    from PIL import Image, ImageDraw, ImageFont

    columns = min(4, len(candidates))
    tile_w, tile_h = 240, 200
    sheet = Image.new(
        "RGB",
        (columns * tile_w, ((len(candidates) + columns - 1) // columns) * tile_h),
        "#20242c",
    )
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("msyh.ttc", 14)
    except OSError:
        font = ImageFont.load_default()
    for i, el in enumerate(candidates):
        tx, ty = (i % columns) * tile_w, (i // columns) * tile_h
        l = max(0, int(el.get("left", el["x"] - 30) - origin[0]) - 12)
        t = max(0, int(el.get("top", el["y"] - 30) - origin[1]) - 12)
        r = min(image.width, int(el.get("right", el["x"] + 30) - origin[0]) + 12)
        b = min(image.height, int(el.get("bottom", el["y"] + 30) - origin[1]) + 12)
        crop = image.crop((l, t, r, b))
        crop.thumbnail((220, 130))
        if crop.width and crop.height:
            scale = min(220 / crop.width, 130 / crop.height, 3)
            enlarged = crop.resize(
                (max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))
            )
            sheet.paste(enlarged, (tx + (tile_w - enlarged.width) // 2, ty + 32))
            enlarged.close()
        draw.text(
            (tx + 8, ty + 5), f"{el['id']}  {el['cell']}", font=font, fill="#8cddff"
        )
        draw.text(
            (tx + 8, ty + 167),
            str(el.get("name") or el.get("type", ""))[:24],
            font=font,
            fill="white",
        )
        crop.close()
    return sheet
