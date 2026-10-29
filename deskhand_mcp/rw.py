"""
rw.py — 精确读写层：UIA Value/Text pattern 读取 + 身份校验 + 安全写入。

设计原则（与 DeskHand 一致）：
- 能走结构化通道的绝不走像素：读取用 ValuePattern / TextPattern，逐字精确；
- 写之前先校验控件身份（名称 / 矩形 IoU），身份不符报 stale 而不是写错地方；
- 默认追加语义（append_text），整篇覆盖必须显式确认（set_value + confirm_overwrite）。

所有函数为同步阻塞，约定在 desktop.run() 的专用 STA 线程中调用。
"""

from __future__ import annotations

from typing import Optional

from .engine import desktop, uia


class RwError(RuntimeError):
    """读写层错误。message 面向调用方（主模型/用户），可直接展示。"""


def _norm(s: Optional[str]) -> str:
    """行尾归一化（RichEdit 会把 \n 存成 \r\n；比较前统一，避免误报不一致）。"""
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _resolve_target(win, element=None, target="", x=None, y=None) -> dict:
    """把 (element | target | x,y) 解析为窗口内一个点 + 期望身份，返回
    {x, y, expect_name, expect_rect} 或抛 RwError。"""
    if element:
        from .engine import scene
        el = scene.resolve(element)
        if el is None:
            raise RwError(f"元素「{element}」不存在或快照已过期（>120s），请重新 look")
        if not scene.matches_window(win["hwnd"]):
            raise RwError("stale：元素快照属于另一个窗口，请对目标窗口重新 look")
        rect = None
        if all(el.get(k) is not None for k in ("left", "top", "right", "bottom")):
            rect = (el["left"], el["top"], el["right"], el["bottom"])
        return {"x": el["x"], "y": el["y"], "expect_name": el.get("name", "") or "",
                "expect_rect": rect, "expect_type": el.get("type", ""),
                "expect_automation_id": el.get("automation_id", ""), "element": el}
    if x is not None and y is not None:
        return {"x": int(x), "y": int(y), "expect_name": target or "",
                "expect_rect": None, "element": None}
    if target:
        # 确定性两级定位：记忆库 → OCR（不走 VL；找不到就交给主模型看图）
        from .engine import locate, memory as mem, ocr
        app_key = desktop.app_key(win)
        bbox = tuple(win["rect"])
        shot = desktop.screenshot(bbox)
        store = locate.get_memory()
        try:
            if store:
                rec = store.lookup(app_key, target)
                if rec:
                    cx = int(bbox[0] + rec["rel_x"] * (bbox[2] - bbox[0]))
                    cy = int(bbox[1] + rec["rel_y"] * (bbox[3] - bbox[1]))
                    sig = mem.crop_signature(shot, cx - bbox[0], cy - bbox[1])
                    if rec["crop_sig"] and store.validate_sig(rec["crop_sig"], sig):
                        return {"x": cx, "y": cy, "expect_name": target,
                                "expect_rect": None, "element": None}
            if ocr.available():
                hit = ocr.find_text(ocr.recognize(shot), target)
                if hit:
                    return {"x": bbox[0] + hit["cx"], "y": bbox[1] + hit["cy"],
                            "expect_name": target,
                            "expect_rect": (bbox[0] + hit["left"], bbox[1] + hit["top"],
                                            bbox[0] + hit["right"], bbox[1] + hit["bottom"]),
                            "element": None}
        finally:
            shot.close()
        raise RwError(
            f"无法定位「{target}」：记忆未命中、OCR 未找到文字。"
            f"请先 look() 查看标注图，再使用最新元素编号或 click(x, y)。")
    raise RwError("必须提供 element / target / x,y 之一")


def find_control(win, *, element=None, target="", x=None, y=None,
                 want_action=False) -> tuple:
    """按元素/目标/坐标解析出 (ctrl, pats, hit)。身份不符抛 RwError(stale)。

    返回的 ctrl 为 uiautomation Control，pats 为可用 pattern 集合。
    """
    if not uia.available():
        raise RwError("未安装 uiautomation（pip install uiautomation），UIA 通道不可用")
    t = _resolve_target(win, element, target, x, y)
    hwnd, win_rect = win["hwnd"], tuple(win["rect"])
    controls = uia._collect_controls(hwnd, win_rect)["controls"]
    hit = uia._nearest(controls, t["x"], t["y"], require_action=want_action)
    if hit is None:
        raise RwError(
            f"({t['x']},{t['y']}) 附近没有可操作的 UIA 控件。"
            f"该软件可能未暴露控件树（Electron/自绘/游戏），请用 look() 看图后 x,y 直点。")
    if not uia._same_target(
            hit, t["expect_name"], t["expect_rect"],
            t.get("expect_type", ""), t.get("expect_automation_id", "")):
        raise RwError(
            f"stale：该位置现在的控件是「{hit['name'] or hit['type']}」（{hit['type']}），"
            f"与期望的「{t['expect_name'] or '未命名'}」不符。请重新 look 后再操作。")
    ctrl, pats = uia._resolve_action_control(
        hit, hwnd, win_rect, t["expect_name"], t["expect_rect"],
        want_action=want_action, expect_type=t.get("expect_type", ""),
        expect_automation_id=t.get("expect_automation_id", ""))
    if ctrl is None:
        raise RwError("无法取得该位置的可操作控件（点定位与逐节点重扫均未命中）")
    return ctrl, pats, hit


def read_value(win, *, element=None, target="", x=None, y=None) -> dict:
    """ValuePattern 精确读取控件值（输入框/文档等）。返回 {value, source, control}。"""
    ctrl, pats, hit = find_control(win, element=element, target=target, x=x, y=y)
    if "value" not in pats:
        raise RwError(f"控件「{hit['name'] or hit['type']}」不支持 ValuePattern"
                      f"（可用 pattern: {sorted(pats) or '无'}），请改用 read_document")
    v = ctrl.GetPattern(uia._u().PatternId.ValuePattern).Value
    return {"ok": True, "value": v, "source": "value_pattern",
            "control": {"name": hit["name"], "type": hit["type"]},
            "char_len": len(v or "")}


def read_selection(win, *, element=None, target="", x=None, y=None) -> dict:
    """通过 TextPattern 精确读取当前选区，不触碰系统剪贴板。"""
    ctrl, pats, hit = find_control(win, element=element, target=target, x=x, y=y)
    name = hit["name"] or hit["type"]
    try:
        pattern = ctrl.GetPattern(uia._u().PatternId.TextPattern)
        ranges = pattern.GetSelection()
        if hasattr(ranges, "Length"):
            ranges = [ranges.GetElement(index) for index in range(ranges.Length)]
        parts = [item.GetText(-1) for item in ranges]
        text = "".join(parts)
        return {"ok": True, "text": text, "source": "text_pattern_selection",
                "exact": True, "char_len": len(text),
                "control": {"name": name, "type": hit["type"]}}
    except Exception as exc:
        raise RwError(f"控件「{name}」不支持 TextPattern 选区读取: {exc}")


def read_document(win, *, element=None, target="", x=None, y=None) -> dict:
    """TextPattern 读取文档全文（DocumentRange.GetText(-1)），逐级降级：
    TextPattern → ValuePattern → OCR（如实标注 source，OCR 结果不保真）。
    """
    ctrl, pats, hit = find_control(win, element=element, target=target, x=x, y=y)
    name = hit["name"] or hit["type"]
    # 1) TextPattern（纯文本全文，逐字精确）
    try:
        _U = uia._u()
        pattern = ctrl.GetPattern(_U.PatternId.TextPattern)
        if pattern:
            text = pattern.DocumentRange.GetText(-1)
            return {"ok": True, "text": text, "source": "text_pattern", "exact": True,
                    "control": {"name": name, "type": hit["type"]},
                    "char_len": len(text or "")}
    except Exception:
        pass
    # 2) ValuePattern
    if "value" in pats:
        v = ctrl.GetPattern(uia._u().PatternId.ValuePattern).Value
        return {"ok": True, "text": v, "source": "value_pattern", "exact": True,
                "control": {"name": name, "type": hit["type"]},
                "char_len": len(v or "")}
    # 3) OCR 兜底（不保真，如实告知）
    from .engine import ocr
    if not ocr.available():
        raise RwError(f"控件「{name}」既无 TextPattern/ValuePattern，本地 OCR 也未安装，无法读取")
    rect = hit.get("rect")
    shot = desktop.screenshot(rect)
    try:
        items = ocr.recognize(shot)
        text = " ".join(str(it["text"]) for it in items if not it.get("line"))
    finally:
        shot.close()
    return {"ok": True, "text": text, "source": "ocr_fallback", "exact": False,
            "warning": "OCR 读取不保真（实测中文混合串字符准确率约 63%），关键数据请勿直接采信",
            "control": {"name": name, "type": hit["type"]},
            "char_len": len(text or "")}


def append_text(win, text: str, *, element=None, target="", x=None, y=None,
                separator: str = "\n") -> dict:
    """安全追加：读旧值 → 校验身份 → 旧值 + separator + text → SetValue → 回读逐字验证。

    绝不覆盖已有内容；任何一步身份不符都会报 stale 而中止。
    """
    if not text:
        raise RwError("text 不能为空")
    ctrl, pats, hit = find_control(win, element=element, target=target, x=x, y=y)
    if "value" not in pats:
        raise RwError(f"控件「{hit['name'] or hit['type']}」不支持 ValuePattern，无法后台追加；"
                      f"可用 pattern: {sorted(pats) or '无'}")
    vp = ctrl.GetPattern(uia._u().PatternId.ValuePattern)
    old = vp.Value or ""
    sep = "" if not old or old.endswith("\n") else separator
    expected = old + sep + text
    focus_before = uia._foreground_hwnd()
    vp.SetValue(expected)
    import time
    time.sleep(0.2)
    after = ctrl.GetPattern(uia._u().PatternId.ValuePattern).Value
    focus_after = uia._foreground_hwnd()
    return {"ok": True, "appended": len(text), "old_len": len(old),
            "new_len": len(after or ""),
            # 行尾归一化后比较：RichEdit 会把 \n 存成 \r\n，属框架行为不是数据损坏
            "verified": _norm(after) == _norm(expected),
            "value_tail": (after or "")[-80:],
            "focus_changed": focus_before is not None and focus_after is not None
                             and focus_before != focus_after}


def set_value(win, text: str, *, confirm_overwrite: bool = False,
              element=None, target="", x=None, y=None) -> dict:
    """整篇覆盖写入。必须 confirm_overwrite=True 显式确认，否则拒绝执行。

    覆盖是危险操作（DeskHand 实测过 SetValue 会静默清空原文）：默认关闭，
    追加请用 append_text。
    """
    if not confirm_overwrite:
        raise RwError(
            "拒绝整篇覆盖：SetValue 会替换控件全部内容。如需追加请用 append_text；"
            "确认要覆盖请传 confirm_overwrite=true。")
    if not text:
        raise RwError("text 不能为空")
    ctrl, pats, hit = find_control(win, element=element, target=target, x=x, y=y)
    if "value" not in pats:
        raise RwError(f"控件「{hit['name'] or hit['type']}」不支持 ValuePattern"
                      f"（可用 pattern: {sorted(pats) or '无'}）")
    focus_before = uia._foreground_hwnd()
    vp = ctrl.GetPattern(uia._u().PatternId.ValuePattern)
    old_len = len(vp.Value or "")
    vp.SetValue(text)
    import time
    time.sleep(0.2)
    after = ctrl.GetPattern(uia._u().PatternId.ValuePattern).Value
    focus_after = uia._foreground_hwnd()
    return {"ok": True, "overwritten": True, "old_len": old_len,
            "new_len": len(after or ""),
            "verified": _norm(after) == _norm(text),
            "focus_changed": focus_before is not None and focus_after is not None
                             and focus_before != focus_after}
