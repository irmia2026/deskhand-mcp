"""
test_client.py — Deskhand-mcp 端到端实测（真连 stdio server，真实操控记事本）。

流程：
1. tools/list 验证注册
2. look(window="Notepad") → 保存标注图
3. click(element=新标签页按钮) 新建标签（绝不碰 SELF-BRIEF 标签）
4. append_text 后台追加 → 要求 verified=true
5. read_document 读回 → 逐字比对
6. set_value 不带 confirm → 必须报错
7. set_value 带 confirm_overwrite=true → 覆盖并回读验证
8. 清理：ctrl+w 关闭标签 → 对话框 OCR 找「不保存」点击

用法: python test_client.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "test_artifacts")
os.makedirs(ART, exist_ok=True)

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def save_img(b64: str, name: str):
    p = os.path.join(ART, name)
    with open(p, "wb") as f:
        f.write(base64.b64decode(b64))
    return p


async def call(session: ClientSession, tool: str, args: dict) -> dict:
    res = await session.call_tool(tool, args)
    text = ""
    img = None
    for blk in res.content:
        if blk.type == "text":
            text = blk.text
        elif blk.type == "image":
            img = blk.data
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw": text}
    return data, img


def find_el(elements: list, *keywords: str) -> dict | None:
    """先精确匹配，再子串匹配（避免「编辑」菜单抢占「文本编辑器」控件）。"""
    lowered = [(el, (el.get("name") or "").lower()) for el in elements]
    for el, name in lowered:
        if any(k.lower() == name for k in keywords):
            return el
    for el, name in lowered:
        if any(k.lower() in name for k in keywords):
            return el
    return None


DIALOG_KW = ("不保存", "Don't Save", "不儲存", "Don't save", "Do not save")


def _match_dialog(name: str) -> bool:
    """OCR 对对话框按钮常输出「不 保 存」（字间带空格）——匹配前去空格。"""
    compact = str(name).replace(" ", "").replace("\u3000", "").lower()
    return any(k.replace(" ", "").lower() in compact for k in DIALOG_KW)


async def _active_tab_is_test(s: ClientSession) -> bool:
    d, _ = await call(s, "window_action", {"action": "focus", "title": "Notepad"})
    t = str(d.get("title", ""))
    return any(marker in t for marker in (
        "deskhand-mcp-e2e", "select-me-line", "capability", "中文😀"))


async def dismiss_save_dialog(s: ClientSession, retries: int = 3) -> bool:
    """关闭「是否保存」对话框（选「不保存」）。

    Notepad 的对话框是 XAML 内嵌对话框：无独立 hwnd、UIA 遍历漏按钮、
    OCR 对按钮小字时有时无——最可靠的是键盘：对话框默认聚焦「保存」，
    Right →「不保存」，Enter 确认。元素匹配点击只作兜底。
    """
    for _ in range(retries):
        # 键盘路径
        await call(s, "press_key", {"keys": ["right"]})
        await call(s, "press_key", {"keys": ["enter"]})
        await asyncio.sleep(1.0)
        if not await _active_tab_is_test(s):
            return True
        # 元素兜底路径（OCR 去空格匹配；按钮在图上但通道不稳）
        for scope in ({"window": "Notepad"}, {}):
            data, _ = await call(s, "look", dict(scope))
            btns = [e for e in (data.get("elements") or []) if _match_dialog(e.get("name") or "")]
            if not btns:
                continue
            b = btns[0]
            await call(s, "click", {"x": b["x"], "y": b["y"]})
            await asyncio.sleep(1.0)
            if not await _active_tab_is_test(s):
                return True
    return not await _active_tab_is_test(s)


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "deskhand_mcp.server"],
        cwd=HERE,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            print("== initialize 完成 ==")

            tools_res = await s.list_tools()
            names = {t.name for t in tools_res.tools}
            want = {"look", "scene_grid", "click", "verify_point", "type_text",
                    "read_value", "read_selection", "read_document", "append_text", "set_value",
                    "press_key", "scroll", "drag", "drag_path", "wait_change", "window_action",
                    "list_windows", "list_apps", "launch_app", "element_action", "agent_control"}
            check("tools/list 21 个工具", names == want,
                  f"缺 {want - names} 多 {names - want}" if names != want else "")

            # 始终新建独立测试标签，绝不复用或关闭用户的未保存草稿
            data, _ = await call(s, "window_action",
                                 {"action": "restore", "title": "Notepad"})
            data, _ = await call(s, "window_action",
                                 {"action": "focus", "title": "Notepad"})
            need_new_tab = True

            # 0.5) 关掉历史遗留的含测试文本的标签（防上次失败残留干扰污染检查）
            for _ in range(6):
                data, _ = await call(s, "look", {"window": "Notepad"})
                els = data.get("elements") or []
                dirty = [e for e in els if any(marker in (e.get("name") or "")
                         for marker in ("deskhand-mcp-e2e", "select-me-line", "capability"))]
                if not dirty:
                    break
                print(f"  清理遗留测试标签: {dirty[0].get('name', '')[:30]}")
                # 真实鼠标点标签（activate），UIA invoke 对 selectionitem 不保证切换
                await call(s, "click", {"x": dirty[0]["x"], "y": dirty[0]["y"]})
                await asyncio.sleep(0.5)
                await call(s, "press_key", {"keys": ["ctrl", "w"]})
                await asyncio.sleep(0.8)
                await dismiss_save_dialog(s)

            # 2) look
            print("== look(Notepad) ==")
            data, img = await call(s, "look", {"window": "Notepad"})
            check("look ok", data.get("ok") is True, str(data.get("error", ""))[:200])
            if img:
                save_img(img, "01_look_annotated.png")
                print(f"  标注图 -> {os.path.join(ART, '01_look_annotated.png')}")
            check("look 带图", img is not None)
            elements = data.get("elements") or []
            check("look 元素非空", len(elements) > 0, f"{len(elements)} 个")
            editor = find_el(elements, "文本编辑器", "Text Editor", "编辑")
            check("找到编辑器控件", editor is not None,
                  "候选: " + "; ".join(f"{e['id']}:{e.get('name')}" for e in elements[:12]))
            tabbtn = find_el(elements, "新标签页", "New tab", "添加")
            check("找到新标签页按钮", tabbtn is not None)

            # 3) 已有其他标签时新建标签；全新实例直接用「无标题」标签
            if need_new_tab:
                print("== click 新标签页 ==")
                data, _ = await call(s, "click", {"element": tabbtn["id"]})
                check("新建标签点击 ok", data.get("ok") is True, str(data.get("error", ""))[:200])
                print(f"  verdict: {data.get('verdict')} / {data.get('verdict_text')}")
                await asyncio.sleep(1.0)

            # 重新 look 拿新快照
            data, _ = await call(s, "look", {"window": "Notepad"})
            editor = find_el(data.get("elements") or [], "文本编辑器", "Text Editor", "编辑")
            check("新快照里仍有编辑器", editor is not None)

            MARK = "deskhand-mcp-e2e-验证行-请勿保存"

            # 4) append_text
            print("== append_text ==")
            data, _ = await call(s, "append_text",
                                 {"text": MARK, "window": "Notepad",
                                  "element": editor["id"]})
            check("append ok", data.get("ok") is True, str(data.get("error", ""))[:300])
            check("append 回读逐字验证", data.get("verified") is True,
                  f"tail={str(data.get('value_tail'))[-60:]!r}")

            # 5) read_document 精确读回
            print("== read_document ==")
            data, _ = await call(s, "read_document",
                                 {"window": "Notepad", "element": editor["id"]})
            check("read_document ok", data.get("ok") is True, str(data.get("error", ""))[:300])
            check("read_document exact", data.get("exact") is True,
                  f"source={data.get('source')}")
            text = data.get("text") or ""
            check("读回内容逐字包含追加文本", MARK in text,
                  f"len={len(text)} head={text[:40]!r}")

            # 6) set_value 不带 confirm 必须被拒绝
            print("== set_value 无确认（应拒绝）==")
            data, _ = await call(s, "set_value",
                                 {"text": "覆盖内容", "window": "Notepad",
                                  "element": editor["id"]})
            check("无 confirm 被拒绝", data.get("ok") is False and "拒绝" in str(data.get("error", "")),
                  str(data.get("error", ""))[:150])

            # 7) set_value 带 confirm 覆盖
            print("== set_value confirm_overwrite=true ==")
            data, _ = await call(s, "set_value",
                                 {"text": MARK + "\n覆盖后的全文", "confirm_overwrite": True,
                                  "window": "Notepad", "element": editor["id"]})
            check("覆盖执行 ok", data.get("ok") is True, str(data.get("error", ""))[:300])
            check("覆盖回读验证", data.get("verified") is True,
                  f"new_len={data.get('new_len')}")
            data, _ = await call(s, "read_document",
                                 {"window": "Notepad", "element": editor["id"]})
            got = (data.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
            check("覆盖后全文正确", got == MARK + "\n覆盖后的全文",
                  repr((data.get("text") or "")[:60]))

            # 8) 清理：点编辑器确保焦点在记事本 → 关标签 → 对话框点「不保存」
            print("== 清理（关闭标签，不保存）==")
            await call(s, "click", {"x": editor["x"], "y": editor["y"]})
            await asyncio.sleep(0.3)
            await call(s, "press_key", {"keys": ["ctrl", "w"]})
            await asyncio.sleep(1.5)
            gone = await dismiss_save_dialog(s)
            check("已关闭测试标签（不保存）", gone)

            await asyncio.sleep(0.5)
            # 污染检查：全新 look 后读 SELF-BRIEF 编辑器（e1 现在是可读写控件排最前，
            # SELF-BRIEF 的编辑器要在窗口标题匹配它时读）——用窗口标题区分两个标签
            data, _ = await call(s, "window_action",
                                 {"action": "focus", "title": "Notepad"})
            title_now = str(data.get("title", ""))
            data, _ = await call(s, "look", {"window": title_now[:20] or "Notepad"})
            elements = data.get("elements") or []
            ed = find_el(elements, "文本编辑器", "Text Editor")
            if ed:
                data, _ = await call(s, "read_document",
                                     {"window": title_now[:20] or "Notepad",
                                      "element": ed["id"]})
                leftover = data.get("text") or ""
                check("SELF-BRIEF 标签未被污染", MARK not in leftover and data.get("ok") is True,
                      f"ok={data.get('ok')} len={len(leftover)} err={str(data.get('error'))[:80]}")
            else:
                check("SELF-BRIEF 标签未被污染", False, "重 look 后找不到编辑器")

    print(f"\n===== 结果: {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
