# Deskhand-mcp

Windows 桌面操控 MCP server——DeskHand 引擎的独立发行版，采用 **host-as-VL 架构**：
本 server 只做确定性的事（UIA 控件树 / 元素记忆 / OCR / 像素 diff / 精确读写），
一切视觉判断交回主模型——标注图通过 MCP ImageContent 直接进主模型上下文，
**不需要额外的视觉模型**。

从 DeskHand（AstrBot 插件版）到 Deskhand-mcp 的核心变化：

| | DeskHand | Deskhand-mcp |
|---|---|---|
| 定位漏斗 | 记忆 → OCR → VL 三级（需配 VL 模型） | 确定性两级：记忆 → OCR；找不到就把图给主模型看 |
| hover-verify | 调 VL 确认落点 | `verify_point` 画十字准星把局部放大图返回给主模型确认（agent-in-the-loop） |
| 精确读取 | OCR（中文混合串实测准确率约 63%） | `read_value`/`read_document` 走 ValuePattern/TextPattern，逐字精确 |
| 文本写入 | `SetValue` 直接覆盖（实测会静默清空原文） | `append_text` 默认追加 + 回读逐字验证；覆盖必须 `confirm_overwrite=true` |
| 运行环境 | AstrBot 插件 | 任意 MCP host（Claude Code / Kimi Code / 等） |

## 安装

```bash
pip install deskhand-mcp[all]   # 或 pip install -e .[all]
deskhand-mcp                     # stdio server，直接配进 MCP host
```

依赖说明：`mcp`/`pywin32`/`Pillow`/`uiautomation` 为必需；`winsdk`（WinRT OCR）、
`rapidocr-onnxruntime`（OCR 兜底）、`opencv-python-headless`（CV 候选框）、
`windows-capture`（WGC 后台抓图）可选，缺哪个对应通道就静默降级。

## 工具一览（14 个）

**看界面（图随结果返回，主模型自己看图）**

- `look(window="")` — 编号元素卡片（UIA 控件 + OCR 文字 + CV 候选框三通道）
  + 元素标注图（框和编号与卡片一一对应）
- `scene_grid(window="")` — 图形/自绘界面的 3×3 编号网格图 + 格→屏幕坐标映射
- `verify_point(x, y)` — 画十字准星返回 320px 局部放大图，主模型看图确认落点

**操作**

- `click(element="eN" | target="文字" | x, y)` — UIA 控件后台点击不抢焦点；
  element 路径带现场签名校验 + OCR 自愈；动作后像素 diff 验证
- `type_text(text, element=)` — UIA 输入控件走后台安全追加（回读验证），
  否则点击聚焦 + 键鼠输入
- `press_key / scroll / drag / wait_change / window_action` — 按键 / 滚动 /
  拖拽 / 等画面变化 / 窗口管理（min/max/restore/close/focus/move/resize…）

**精确读写（ValuePattern / TextPattern，逐字精确）**

- `read_value(element=)` — 读输入框当前值（ValuePattern）
- `read_document(element=)` — 读文档全文（TextPattern → ValuePattern → OCR 降级，
  OCR 结果如实标 `exact:false`）
- `append_text(text, element=)` — 安全追加：读旧值 → 身份校验 → 追加 →
  SetValue → 回读逐字验证
- `set_value(text, confirm_overwrite=true)` — 整篇覆盖（危险，默认拒绝）

## 在 MCP host 中配置

```json
{
  "mcpServers": {
    "deskhand": {
      "command": "deskhand-mcp"
    }
  }
}
```

未安装为包时可直接用源码运行：

```json
{
  "mcpServers": {
    "deskhand": {
      "command": "python",
      "args": ["-m", "deskhand_mcp.server"],
      "cwd": "C:\\path\\to\\deskhand-mcp"
    }
  }
}
```

记忆库（元素位置 + 图像签名）存于 `%LOCALAPPDATA%\deskhand-mcp\deskhand_memory.db`，
设环境变量 `DESKHAND_DATA_DIR` 可改目录。

## 架构：为什么砍掉 VL

实测对比（见 DeskHand 深度评测）：VL 漏斗最贵的一环（每点一次都要调视觉模型）
恰恰是最不可靠的一环——黑盒坐标、不可解释、延迟高。而现代主模型基本都已多模态，
看图能力远超专用小 VL。host-as-VL 的思路：**把「看」还给主模型，把「做」做扎实**——

- 能确定性的绝不给模型猜：UIA 控件树、OCR 文字、像素 diff、ValuePattern 回读；
- 必须视觉判断的地方，把图直接塞进主模型上下文（标注图/网格图/准星放大图），
  由主模型下结论或给修正量——agent-in-the-loop，每一步都可解释；
- 精确读写绕过像素层：TextPattern 的 `DocumentRange.GetText(-1)` 读全文逐字精确，
  彻底告别「看图识字」的保真度问题。

## 安全边界

- UAC 提权窗口、以管理员运行的窗口：UIA/win32 无法穿透，工具会如实报错；
- 最小化窗口 UIA 树为空：先 `window_action(action="restore")`；
- `set_value` 覆盖写入默认拒绝，必须显式 `confirm_overwrite=true`；
- 目标身份不符（弹窗遮挡/界面移动）时报 stale 中止，绝不盲点旧坐标。

## 协议

AGPL-3.0-only，与 [DeskHand](https://github.com/irmia2026/astrbot_plugin_deskhand) 一致。
