"""
server.py — Deskhand-mcp：MCP stdio server 组装层。

host-as-VL 架构：本 server 只做确定性的事（UIA 控件树 / 元素记忆 / OCR /
像素 diff / 精确读写），一切视觉判断交回主模型——标注图通过 MCP ImageContent
直接进主模型上下文，不需要额外的 VL 模型。

运行：python -m deskhand_mcp.server   或安装后 deskhand-mcp
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import sys

import mcp.types as mt
from mcp.server.mcpserver import MCPServer

from . import rw, tools
from .engine import desktop, locate, memory as mem

logger = logging.getLogger("deskhand_mcp.server")

_DATA_DIR = os.environ.get(
    "DESKHAND_DATA_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                 "deskhand-mcp"),
)


def _memory_db_path() -> str:
    os.makedirs(_DATA_DIR, exist_ok=True)
    return os.path.join(_DATA_DIR, "deskhand_memory.db")


def _wrap(fn):
    """把工具函数包成统一的 content-block 返回：dict 转 JSON 文本，"_image" 剥成图。"""

    @functools.wraps(fn)
    async def _call(**kwargs):
        try:
            result = await fn(**kwargs)
        except rw.RwError as e:
            result = {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 —— 工具错误必须以结构化结果返回，不能断连
            logger.warning("工具 %s 执行失败: %s", fn.__name__, e)
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        image = result.pop("_image", None)
        text = json.dumps(result, ensure_ascii=False, default=str)
        blocks: list = [mt.TextContent(type="text", text=text)]
        if image:
            import base64

            blocks.append(mt.ImageContent(
                type="image",
                data=base64.b64encode(image).decode("ascii"),
                mime_type="image/png",
            ))
        return blocks

    return _call


def build_server() -> MCPServer:
    """初始化记忆库并注册全部 14 个工具。"""
    # 记忆库默认开启（SQLite，env DESKHAND_DATA_DIR 可覆盖路径）
    store = None
    try:
        store = mem.ElementMemory(_memory_db_path())
        logger.info("元素记忆库已开启: %s", _memory_db_path())
    except Exception as e:  # noqa: BLE001
        logger.warning("元素记忆库初始化失败（不影响其他功能）: %s", e)
    locate.set_memory(store)

    tools.setup({
        "memory_enabled": True,
        "ocr_enabled": True,
        "uia_enabled": True,
        "ocr_multiscale": "auto",
        "input_method": "auto",
        "post_action_wait": 0.4,
        "min_change_percent": 0.05,
        # legacy：DeskHand 时代的 VL 配置。host-as-VL 架构下视觉判断由主模型
        # 看标注图完成，本 server 不再调用 VL 模型；保留此键仅为配置兼容。
        "vl_model": None,
    })

    server = MCPServer(
        name="deskhand-mcp",
        instructions=(
            "Deskhand-mcp：Windows 桌面自动化（host-as-VL 架构）。\n"
            "工作流：先 look(window=...) 拿编号元素卡片 + 标注图（图随结果返回，"
            "由你自己看图理解界面）；再 click(element=\"eN\") / click(x, y) 操作；"
            "需要精确读写内容时用 read_value / read_document / append_text / "
            "set_value（SetValue 覆盖需 confirm_overwrite=true）；"
            "落点不确定时 verify_point(x, y) 看图确认；图形界面用 scene_grid。"
            "所有坐标为屏幕物理像素，标注图框和编号与 elements_card 一一对应。"
        ),
    )

    def reg(fn):
        server.tool(name=fn.__name__, description=(fn.__doc__ or "").strip(),
                    structured_output=False)(_wrap(fn))

    reg(tools.look)
    reg(tools.scene_grid)
    reg(tools.click)
    reg(tools.verify_point)
    reg(tools.type_text)
    reg(tools.read_value)
    reg(tools.read_document)
    reg(tools.append_text)
    reg(tools.set_value)
    reg(tools.press_key)
    reg(tools.scroll)
    reg(tools.drag)
    reg(tools.wait_change)
    reg(tools.window_action)
    return server


async def _amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    server = build_server()
    try:
        await server.run_stdio_async()
    finally:
        try:
            store = locate.get_memory()
            if store:
                store.close()
        except Exception:
            pass
        desktop.shutdown()


def main() -> None:
    if sys.platform != "win32":
        print("deskhand-mcp 仅支持 Windows（依赖 UIA/win32/WGC）", file=sys.stderr)
        sys.exit(1)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
