"""Deskhand-mcp — 独立 MCP 版桌面操控引擎（host-as-VL 架构）。

由 DeskHand（astrbot_plugin_deskhand，AGPL-3.0，弥亚庄园）引擎独立而来：
- 去掉 AstrBot / 外部 VL 依赖：视觉判断交回主模型（多模态 host-as-VL）
- 新增精确读写层（rw.py）：UIA Value/Text pattern 读取、安全追加写入
- type_text 在光标处输入；append_text 后台追加；覆盖须 confirm_overwrite=True
"""

__version__ = "0.3.1"
