# 局部图与坐标

宿主完全不支持 MCP 图片时，由接入方在服务器启动环境设置 `DESKHAND_IMAGE_OUTPUT=off`；模型调用优先 image=false。不自行修改用户宿主配置，也不把不能处理图片误认为工具没有找到元素。

## 从候选到局部图

1. 有名称就 find_element，按返回编号操作。
2. 无名称但任务明确指向基础外观时，用 color、shape、position 筛选。position 为 A1–C3，不是像素坐标。
3. 多候选时查看候选图集并选编号。**图集是多张裁图拼接，图集坐标不能点击。**
4. 看不清可 `inspect_region(element=实际编号, zoom=3)`；不知道元素时用 cell="B2"。
5. 仍看不清可 `inspect_region(image_id=上张局部图编号, cell="B2", zoom=3)`。这是裁切旧快照，不是获取更新后的实时界面。

放大不能增加原图没有的细节。反复放大同一模糊图无意义；必要时改变应用缩放或窗口大小，再重新观察。

## 三种坐标不可混用

| 来源 | 调用 |
| --- | --- |
| 候选 x,y 或 focus_point.screen_x/screen_y | 屏幕物理像素，click(x=...,y=...)，不带 image_id |
| 返回图片内像素 | click(image_id=对应图片编号,x=...,y=...) |
| click_args | 原样采用其中的 element 参数，不加图集坐标 |

size 是物理截图尺寸，image_size 是返回图片尺寸，通常不同。不手算 DPI，不重复加窗口原点，不把 0–1 比例或 0–1000 归一化值直接当像素。

look(view="grid") 的像素标尺供读图内 x,y；scene_grid 是 A1–C3 粗网格，两者不同。粗格中心只有在任务明确指向该中心时才是目标，不能代表格中任意图标。

inspect_region 的 region=[x,y,w,h] 有 image_id 时针对对应图片，无 image_id 时针对原始快照图。element、cell、region 三者只能选一个。

## 用工具提供的落点

已确认元素就是目标时直接点击编号。确需放大图坐标时，focus_point.image_x/image_y 是该图中的元素中心；取最近整数并检查 `0 <= x < image_size[0]`、`0 <= y < image_size[1]`，连同该图 image_id 点击。上张图的落点不能配下张图的编号。

若宿主再次缩放／裁切图片，先确认可见像素尺寸与 image_size 一致。不能确认时回到编号或再取局部图，不凭显示大小猜换算。

verify_point 提供屏幕落点周边的准星图供观察，不自动判定目标正确，不能替纯文本模型完成语义确认。

窗口移动、缩放、滚动或弹出菜单后重新观察；未满 120 秒也不代表旧布局仍然有效。
