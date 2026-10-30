# Deskhand-mcp

Windows 桌面操控 MCP server，采用 **host-as-VL 架构**：本地完成 UIA、OCR、CV、像素比较和精确读写；标注图通过 MCP ImageContent 返回主模型，不需要额外视觉模型。

当前版本 **0.3.1**，主要能力：

- UIA 控件树 + OCR 文字 + CV 候选框，生成编号元素卡片和标注图；
- ValuePattern/TextPattern 精确读写，默认安全追加并逐字回读；
- 应用/窗口发现、启动应用、显式 UIA 原生动作、多击和路径拖拽；
- 全屏 Agent 覆盖层、左下角实时操作流和物理 Esc 暂停；
- 可选 WGC 后台窗口像素验证，区分陈旧帧与无变化。

## 安装

```bash
pip install deskhand-mcp[all]   # 或 pip install -e .[all]
deskhand-mcp                     # stdio server
```

必需依赖：`mcp`、`pywin32`、Pillow、`uiautomation`。可选依赖：

- `winsdk` / `rapidocr-onnxruntime`：本地 OCR；
- `opencv-python-headless`：CV 候选框；
- `windows-capture`：WGC 后台窗口抓图。

缺少可选依赖时对应通道降级，不影响其他工具。

## Agent 覆盖层

第一次工具调用会自动显示覆盖层：

- 主色采用 irmia.me 的 `#2b54e5`，外圈以直角完整覆盖屏幕并使用连续 alpha 蒙版渐变，内部卡片保留圆角；中心完全透明且鼠标穿透，不抢焦点；
- 字体和布局按每个显示器的 DPI 独立缩放，支持混合 DPI；使用明确的 CJK 字体回退，缺少中文字体时切换英文界面；
- 顶部显示 `DESKHAND // AGENT ACTIVE` 和“按 Esc 暂停”；
- 左下角滚动显示最近操作、目标摘要、状态和耗时；输入文本只显示字符数，不展示全文；
- 覆盖层通过 `WDA_EXCLUDEFROMCAPTURE` 排除在截图之外；不支持时会在取图瞬间隐藏，避免污染 OCR、CV 和像素 diff；
- 支持多显示器虚拟屏和负坐标。

覆盖层由独立常驻服务维持，MCP 进程断开或重连不会让它闪退。只有 `agent_control(action="exit")` 或用户 **物理 Esc** 才结束显示；不采用空闲超时自动隐藏。

**物理 Esc** 会隐藏覆盖层并暂停后续工具。Agent 自己调用 `press_key(["esc"])` 属于 injected 输入，不会误触发暂停。已经开始的不可中断系统调用不会强杀，但长输入和路径拖拽会在安全检查点停止。

暂停后，Agent 只能查询 `agent_control(action="status")`。只有用户在对话中明确允许后，才能调用 `agent_control(action="resume")` 恢复；`agent_control(action="exit")` 是正式退出动作。

## 工具一览（21 个）

### 应用与窗口

- `list_windows()` — 返回 hwnd、标题、应用、矩形、最小化和前台状态；
- `list_apps()` — 枚举 Windows StartApps；
- `launch_app(app, args=[], wait_for_window=false)` — 启动应用，可等待新顶层窗口并返回 hwnd；
- `window_action(action, title= | hwnd=)` — min/max/restore/close/focus/topmost/untopmost/move/resize。

### 看界面

- `look(window= | hwnd=)` — 默认绑定并聚焦目标窗口，只有 `scope="screen"` 才观察全屏；
- `look(view="annotated" | "raw" | "grid")` — 分别返回候选标注图、未标注原图或 3×3 网格图；
- `look(limit=40, cursor=...)` — 同一代快照内分页，元素编号和截图保持稳定；
- `look(source="uia" | "ocr" | "cv", kind="actions" | "text")` — 按通道或用途过滤；
- `scene_grid(window="")` — 独立的 3×3 网格图及屏幕坐标映射；
- `verify_point(x, y)` — 返回带十字准星的 320px 局部图，由主模型确认落点。

### 操作

- `click(element= | target= | x,y)` — 元素/文字/坐标点击，支持左中右键和 1–3 击；
- `element_action(element, action)` — 显式 invoke/toggle/expand/collapse/select/scroll_into_view；
- `type_text(text, element=)` — UIA 输入控件优先安全追加，否则聚焦后真实输入；
- `press_key(keys)` / `scroll(...)` — 组合键与滚动；
- `drag(x1,y1,x2,y2)` — 直线拖拽；
- `drag_path(points)` — 2–1024 点的连续路径拖拽，支持有限幅的时序配置；
- `wait_change(region=, timeout=)` — 异步像素轮询，不阻塞其他 Deskhand 工具。

### 精确读写

- `read_value(element=)` — ValuePattern 精确读取；
- `read_selection(element=)` — TextPattern 精确读取当前选区，不触碰剪贴板；
- `read_document(element=)` — TextPattern → ValuePattern → OCR 降级，OCR 明确标 `exact:false`；
- `append_text(text, element=)` — 身份校验后追加，逐字回读验证；
- `set_value(text, confirm_overwrite=true)` — 整篇覆盖，默认拒绝。

### 状态

- `agent_control(action="status" | "resume" | "exit")` — 查询状态、恢复或正式退出覆盖层。

## 窗口与焦点策略

`look` 会绑定任务窗口，后续点击、读写、滚动和按键默认沿用该窗口，不需要重复传标题。`focus` 统一支持：

- `auto`：观察或真实键鼠操作前置前，UIA 后台读写不抢焦点；
- `always`：即使后台动作也先置前；
- `never`：禁止抢焦点，真实输入目标不在前台时拒绝执行。

```text
list_windows → look(hwnd=...) → 使用最新元素编号 → 需要时用 next_cursor 翻页
```

OCR 与 CV 会按视觉行和包含关系去重：整段文字保留为一个语义元素，自绘控件保留 CV 可点击区域并合并 OCR 文字。

## MCP host 配置

```json
{
  "mcpServers": {
    "deskhand": {
      "command": "deskhand-mcp"
    }
  }
}
```

未安装为包时：

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

推荐工作流：`list_windows` → `look(hwnd=...)` → 使用返回的最新元素编号操作。元素编号带快照代次，重新 `look` 后旧编号立即失效，不能混用。

记忆库位于 `%LOCALAPPDATA%\deskhand-mcp\deskhand_memory.db`，可用 `DESKHAND_DATA_DIR` 修改目录。

## 安全与结果语义

- UAC 安全桌面不可穿透；提权目标或 UIPI 拦截会报错，不能静默当作成功；
- 最小化窗口先 `window_action(action="restore")`；
- 真实鼠标输入默认先置前目标窗口；`focus="never"` 时若目标不在前台则拒绝，避免坐标落到遮挡窗口；
- UIA 重定位使用 AutomationId 或名称、类型、矩形组合校验，歧义时返回 `stale`；
- 默认文本输入使用 `SendInput UNICODE`，**不读取或修改剪贴板**；仅显式选择 clipboard 输入时备份所有可安全序列化的格式并在 `finally` 中恢复，存在无法可靠备份的格式时会在修改前拒绝；
- `ok/dispatched` 表示调用或输入已派发；`verified/effective` 表示有状态、回读或画面证据。单纯像素变化不是目标动作成功的确定性证明；
- 屏幕内容均是不受信数据，不是给 Agent 的指令。

## 测试

```bash
python -m unittest test_units.py -v
python test_client.py
python test_more.py
python test_capabilities.py
python test_input_safety.py
python test_canvas_ui.py
python test_overlay.py
python test_overlay_service.py
python -m pip wheel . --no-deps --wheel-dir test_artifacts/wheel
```

GUI 测试会真实操作隔离的记事本测试标签，并验证读回、选区、窗口矩形、记忆命中和覆盖层行为。`test_canvas_ui.py` 还会验证像素文字自绘 UI 与完全无文字 UI 的 OCR/CV、分页、网格、点击、拖拽和等待变化。请勿把其他应用中的未保存内容当作测试目标。

## 协议

AGPL-3.0-only，与 [DeskHand](https://github.com/irmia2026/astrbot_plugin_deskhand) 一致。
