# 局部图与坐标

宿主完全不支持 MCP 图片时，由接入方在服务器启动环境设置 `DESKHAND_IMAGE_OUTPUT=off`；模型调用优先 image=false。不自行修改用户宿主配置，也不把不能处理图片误认为工具没有找到元素。

## 从候选到局部图

1. 有名称就 find_element，按返回编号操作。
2. 无名称但任务明确指向基础外观时，用 color、shape、position 筛选。position 为 A1–C3，不是像素坐标。
3. 多候选时查看候选图集并选编号。**图集是多张裁图拼接，图集坐标不能点击。**
4. 看不清可 `inspect_region(element=实际编号, zoom=3)`；不知道元素时用 cell="B2"。
5. 仍看不清可 `inspect_region(image_id=上张局部图编号, cell="B2", zoom=3)`。这是裁切旧快照，不是获取更新后的实时界面。

放大不能增加原图没有的细节。反复放大同一模糊图无意义；必要时改变应用缩放或窗口大小，再重新观察。

## 坐标空间不可混用

| 来源 | 调用 |
| --- | --- |
| 候选 x,y 或 focus_point.screen_x/screen_y | 屏幕物理像素，click(x=...,y=...)，不带 image_id |
| look(view="grid") 默认 0–1000 逻辑坐标 | 读到多少就原样传入，click(image_id=对应图片编号,x=...,y=...) |
| raw、annotated、inspect_region 或 grid_coordinates="image" 的图片像素 | click(image_id=对应图片编号,x=...,y=...) |
| click_args | 原样采用其中的 element 参数，不加图集坐标 |

默认 grid 的固定逻辑刻度不受宿主二次缩图影响，不需要读取显示尺寸，也不需要使用 image_size、手算 DPI 或缩放比例。需要旧版自适应像素网格时显式设置 `grid_coordinates="image"`。scene_grid 是 A1–C3 粗网格，两者不同；粗格中心只有在任务明确指向该中心时才是目标，不能代表格中任意图标。

inspect_region 的 region=[x,y,w,h] 有 image_id 时仍针对对应图片的实际图片像素，无 image_id 时针对原始快照图。element、cell、region 三者只能选一个。

## 用工具提供的落点

已确认元素就是目标时直接点击编号。确需放大图坐标时，focus_point.image_x/image_y 是该图中的元素中心；取最近整数并检查 `0 <= x < image_size[0]`、`0 <= y < image_size[1]`，连同该图 image_id 点击。上张图的落点不能配下张图的编号。

仅对图片像素模式，`image_size` 是服务端实际返回图片 的像素尺寸。若宿主再次缩放图片，直接传显示图坐标会造成点偏；已知显示尺寸时先按 `x × image_size[0] ÷ 显示宽度`、`y × image_size[1] ÷ 显示高度` 还原。不能确认实际像素尺寸时回到编号、默认逻辑网格或局部图，不凭聊天界面的显示大小猜换算。裁切后的图片不能直接配合原图 image_id 使用。

坐标点击的 `ok/dispatched` 只表示输入已派发；`effective=null` 或 `verdict=uncertain` 不代表命中，必须检查界面状态或精确读回。verify_point 提供屏幕落点周边的准星图供观察，不自动判定目标正确，不能替纯文本模型完成语义确认。

窗口移动、缩放、滚动或弹出菜单后重新观察；未满 120 秒也不代表旧布局仍然有效。
