# Deskhand 调用示例

需要时参照 [人类 UI 操作案例](human-ui-cases.md)，用此页映射工具调用。参数合法不证明 UI 策略正确；普通熟悉流程无需每次先读参考。

JSON 是调用形状示例，element 必须换成当前结果，不能照抄示例编号。命名空间由宿主决定。

## 表单字段

按类型找，避免把“文件名”静态标签当输入框：

```json
{"tool":"find_element","arguments":{"query":"文件名","control_type":"input","image":false}}
```

唯一且符合任务时设置内容，检查 verified；UIA 不支持才考虑键盘路径：

```json
{"tool":"set_value","arguments":{"element":"e12_7","text":"示例内容","confirm_overwrite":true}}
```

键盘顺序为聚焦 → 全选 → 输入；type_text 不再附带 element，避免重新点击破坏选区：

```json
{"tool":"click","arguments":{"element":"e12_7"}}
```
```json
{"tool":"press_key","arguments":{"keys":["ctrl","a"]}}
```
```json
{"tool":"type_text","arguments":{"text":"示例内容"}}
```

稳定布局下这三步之间不必每次 look。工具已经逐字验证就不另行 read_value；否则按需要在一段操作后检查。输入目标仅是用户指定字段，不是整个应用文档。

字段按应用约定设置后可继续操作；后续按键作用域出现疑问时，再按 [交互状态与作用域](interaction-state.md) 处理。后台 set_value 回读成功不证明 UIA 已帮你失焦；click 的 invoke 也可能只调用功能，没有物理点击的焦点效果。反复读同一个值不能解决焦点疑问。

当前 set_value 不接受空字符串。用户要求清空字段时，聚焦后全选再 Backspace，然后读回确认空值；不要循环尝试 set_value(text="")。

## 文档与选区

选区用 read_selection，不靠剪贴板确认。替换选区用 type_text；追加用 append_text，默认换行分隔，紧接末尾用 separator=""。type_text 不保证无论光标在哪都整字段覆盖。

```json
{"tool":"append_text","arguments":{"element":"e12_7","text":"尾部内容","separator":""}}
```

## 菜单和保存

1. 点击正确菜单，展开后重新观察找子项，不凭旧坐标预排整条路线。
2. 对话框出现后确认目标，必要时 list_windows 后 look 指定对话框 hwnd。
3. 用类型过滤选择文件名输入框，设置任务要求路径并读回。
4. 点击保存。覆盖同名文件提示按原授权处理，不把保存新文件自动扩大成覆盖其他文件。
5. 验证对话框关闭、标题／状态变化；宿主有文件读取能力且路径在任务范围内时，再核实落盘文件和格式／尺寸。不要为了验证重复点击保存。

## 减少调用

可靠名称不必取全屏图或翻几十页。稳定表单可复用本次编号并分别读回；改变布局的动作后重观察。模糊候选不能靠多点几次解决。

不默认加入鼠标动画、随机偏移或缓慢移动。仿人类真正有用的是正确的聚焦、选区、输入顺序和动作后的状态确认。
