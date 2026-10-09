---
format: 1920x1080
duration: 120s
message: "这是一套可自托管的校园打卡自动化工具 —— 但请先看完开头的免责声明再决定用不用"
arc: 免责声明（单页完整·一次呈现） → 片名 → 授权流程 → 功能与用法 → 部署 → AI 辅助 → 收尾
audience: 打开 GitHub 仓库、有一定技术基础的同学与开发者
mode: collaborative
music: none
---

## Frame 1 — 免责声明（单页完整·开场）

- scene: 开场即是一整页免责声明，七条条文一次性全部呈现，静置供阅读
- duration: 34s
- poster: 6s
- transition_in: cut
- status: outline
- voiceover: "先看这份免责声明。七条：与学校无关；可能违反校规；遵守法律法规；风险自负；坐标不代表真实位置；按现状提供不作担保；责任限制。"
- blueprint: compose
- focal: 七条条文的紧凑网格整体
- roles: 条文网格 = foreground subject（占满内容区）· 编号列 = supporting · 结论行 = supporting
- sfx: chime
- src: compositions/frames/02-disclaimer-all.html

**这是全片最要紧的一帧，也是唯一信息密度高的帧，同时是开场第一帧。** 目标：**完整七条在一页内一次性全部呈现**，不删减任何条文、不做逐条揭示。排版像一份正式文件而非警告弹窗 —— 靠栅格、发丝线与留白建立秩序，不靠颜色。

信息结构（全部条文逐字保留，来自仓库 `DISCLAIMER.md`）：

- 页眉 kicker：`免责声明`（coral，等宽大写，带 `*`）
- 标题（EB Garamond）：`使用前请先读完`
- **七条网格**：3 列布局。每条 = 等宽珊瑚编号 + 一行小标题 + 1–2 行正文（Inter，ink 76%，约 1.16cqw）
  1. `与学校无关` — 本项目与任何学校、任何第三方服务提供商均无关联，未获授权、认可或支持
  2. `可能违反校规` — 「代打卡」可能被认定为违纪行为，可能导致纪律处分
  3. `遵守法律法规` — 需遵守网络安全法、数据安全法、个人信息保护法及所在地法律
  4. `风险自负` — 使用本项目的风险由使用者自行承担
  5. `坐标不代表真实位置` — 提交的是学校登记的宿舍基准点，定位校验距离为 0 米
  6. `按现状提供` — 不作任何担保：接口不变更、签到必成功、服务可用、数据不丢失
  7. `责任限制` — 在适用法律允许的最大范围内，作者不承担任何责任
- 页脚结论行（发丝线上方，Inter，ink 62%）：`仅供学习与技术研究使用。继续使用即表示你已阅读并理解上述全部条款。`

动效（**关键：一次放完，不再逐条落定**）：
Scene 1 (0.0–1.4s): 页眉 kicker、标题、**七条全部条文、页脚结论行**在同一拍内一起落定 —— 用极短的错位（每条间隔 0.03 秒，总计不到 0.25 秒）做出"整页落下"的观感，而不是逐条宣读。chime 落在整页落定那一刻。
Scene 2 (1.4–2.0s): 全部就位，画面稳定。
Scene 3 (2.0–30.0s): **静置 28 秒**供阅读。不做任何位移、不做呼吸动效、不加装饰性动效 —— 这一帧的任务是被读完，不是被观看。

备注：因为条文是一次性呈现，"逐条落定"的原设计已被本帧取消；网格高度按**静态全显**计算（内容底边 ≈798px，留出 100px 安全余量）。

## Frame 2 — 开场与片名

- scene: 终端窗口里光标闪烁，打出一行克隆命令，随后片名与警示行浮出
- duration: 10s
- poster: 8s
- transition_in: crossfade
- status: outline
- voiceover: "中南林业科技大学平安打卡自动签到 —— 一套可自托管的工具，仅供学习使用。"
- blueprint: compose
- focal: 深色终端面板中的逐字命令行 + 其下的片名
- roles: terminal panel = foreground subject · cream field = background · kicker + warning line = supporting
- sfx: typing, impact-bass-1
- src: compositions/frames/01-title.html

**浅色场**（暖米 `#FAF9F5`，非纯白）。看完免责声明后进入片名 —— 这时的语气是"现在说清楚这是什么"。
Scene 1 (0.0–1.7s): centered，仅一个深色终端面板（约 62% 宽），暖米色空场，面板内三颗窗口点 + 一个闪烁的 coral 光标。无其它内容。
Scene 2 (1.7–4.7s): 命令行逐字打出 `$ git clone .../csuft-auto-checkin`，随后第二行 `$ cd csuft-auto-checkin`（typing，落在 beat 上）。面板保持居中。
Scene 3 (4.7–7.2s): 面板下方浮出 kicker 索引行「仅供学习使用」（coral），随片名「平安打卡自动签到」以大字号入场（impact-bass-1 落在片名落定那一刻）。
Scene 4 (7.2–10.0s): 警示行「使用前请先读完免责声明」淡入，光标停住不闪。整体静止读完，不做位移。

## Frame 3 — 怎么接入：授权与绑定

- scene: 四步流程图横向铺开 —— 同学跑抓包工具、拿到结果、发给管理员、管理员批准后开始自动签到
- duration: 16s
- poster: 13s
- transition_in: crossfade
- status: outline
- voiceover: "接入分四步。同学在自己电脑上跑一次抓包小工具，拿到一份登录凭证，发给管理员；管理员核对后批准，之后就会每天自动签到。"
- blueprint: compose
- focal: 横向四步流程图，逐步点亮
- roles: 流程四步 = foreground subject（占满宽度）· 步骤之间的连接线 = supporting · 底部补充说明 = supporting
- sfx: pop, click-soft, ping
- src: compositions/frames/03-onboarding.html

**这是"用法"的核心一帧** —— 全片的流程空缺在此补齐。版式用横向四步条带，与前后帧区分。
Scene 1 (0.0–1.8s): 上部三分位。kicker「怎么接入」与标题「四步开始自动签到」入场。
Scene 2 (1.8–4.2s): 流程轨道在中部横向铺开（draw 自左向右），四个步骤的**空位编号** 01–04 先落位。
Scene 3 (4.2–12.6s): 四步依次落定，每步约 2.1 秒：
  1. `跑一次小工具` — 在 Windows 上双击运行，自动装好证书与代理
  2. `拿到登录凭证` — 工具把抓到的会话写入 `token-result.txt`
  3. `发给管理员` — 把结果文件交给管理员核对
  4. `批准后自动签到` — 管理员确认后加入名单，每天自动执行
  每步 = 等宽珊瑚编号 + 小标题 + 一行说明；步骤之间的连接线随该步落定而延长（draw）。pop 落在每步编号，ping 落在第四步。
Scene 4 (12.6–16.0s): 底部一行补充说明淡入（Inter，ink 62%）：
  `凭证只保存在你自己的服务器上，工具不上传任何数据`
  随后整体静止读完。

## Frame 4 — 它能做什么

- scene: 两张界面卡片并排上移入场，各自要点逐条弹出；右上角浮出一个"随机时刻"徽标
- duration: 16s
- poster: 12s
- transition_in: crossfade
- status: outline
- voiceover: "工具分两部分。用户端提交申请、绑定学校账号、查看自己的签到日历。管理端由管理员亲自审批每一个申请 —— 有且仅有管理员本人的账号能登录。"
- blueprint: compose
- focal: 左卡片「用户网页 /」与其要点
- roles: 左卡片 = foreground subject · 右卡片 = supporting（稍后入场）· cream field = background
- sfx: pop, click-soft, chime
- src: compositions/frames/06-capabilities.html

语速从这里开始轻快一点，但仍是文档语气，不是营销。**动效比免责帧丰富**：卡片要做分层推进，而不是整块淡入。
Scene 1 (0.0–1.6s): kicker「它由两部分组成」入场，两张 tile 色空卡框已在位（发丝描边先画）。
Scene 2 (1.6–7.6s): 左卡片填充：等宽标签「用户网页 /」→ 标题「提交申请，等管理员批准」→ 三条要点依次 pop-in（「绑定学校打卡账号」「每天自动签到」「日历看板查看自己的记录」），pop 音效落在每条。
Scene 3 (7.6–12.4s): 右卡片延后填充：等宽标签「管理员网页 /admin」→ 标题「有且仅有管理员本人可登录」→ 三条要点依次 pop-in（「亲自审批每一个申请」「成员管理与手动签到」「运行监控与日志」）。右卡片整体低对比一档。
Scene 4 (12.4–15.0s): 卡片底部各画出一条发丝线收束，随后静止读完（chime 落在收束时）。

## Frame 5 — 怎么用：一天的流程

- scene: 时间轴从 21:00 铺到 22:30，随机时刻落下一个标记并亮起，下方展开寒暑假说明
- duration: 14s
- poster: 10s
- transition_in: crossfade
- status: outline
- voiceover: "每天在 21:00 到 22:30 之间的随机时刻自动执行。系统跟随学校任务的打卡期间，自动覆盖寒暑假 —— 寒假留校也照常打卡。"
- blueprint: compose
- focal: 横向时间轴与落下的随机时刻标记
- roles: 时间轴 = foreground subject（占满宽度）· 刻度与标签 = supporting · 下方说明 = supporting
- sfx: whoosh-short, chime
- src: compositions/frames/07-daily-flow.html

全宽条带版式，与前后帧的卡片/居中构图区分开。
Scene 1 (0.0–1.8s): 上部三分位。kicker「每天怎么跑」与标题「在 21:00 – 22:30 之间随机执行」入场。
Scene 2 (1.8–4.2s): 时间轴在中部横向铺开（draw，自左向右），两端刻度标注 21:00 / 22:30，中段刻度逐个浮现。
Scene 3 (4.2–7.4s): 一个 coral 标记在 44% 处落下并亮起（whoosh-short 落在落下瞬间，chime 落在亮起），标记上方浮出该时刻数值 `21:47`。
Scene 4 (7.4–13.0s): 轴下方展开说明「跟随学校任务的打卡期间 —— 自动覆盖寒暑假，留校也照常打卡」，其中「覆盖寒暑假」画 coral 下划线（scaleX draw）。稳定静止读完。

## Frame 6 — 部署：本地与云服务器

- scene: 左右两栏对照，云部署命令逐字敲出，流程行逐级点亮
- duration: 16s
- poster: 13s
- transition_in: crossfade
- status: outline
- voiceover: "部署有两条路。本地：装依赖、跑起来，两行命令。云服务器：一条命令完成预检、上传、装依赖、配置 systemd 与启动验证。服务器需要在中国大陆。"
- blueprint: compose
- focal: 右栏云部署命令与其流程行
- roles: 左栏本地 = supporting（简单，先出）· 右栏云部署 = foreground subject（重头）· 两个深色代码面板 = midground
- sfx: typing, key-press, ping
- src: compositions/frames/08-deploy.html

信息量最大的一帧，两栏必须主次分明。**动效最丰富的一帧**：命令逐字敲、流程行逐级点亮。
Scene 1 (0.0–1.6s): 对称分屏。kicker「两种部署方式」入场，两栏标题「本地」/「云服务器」先落位，代码面板为空框（发丝描边先画）。
Scene 2 (1.6–5.4s): 左栏快速完成：深色代码面板内两行命令淡入（`pip install -r requirements.txt`、`python run.py`），下方小字「两行命令，打开 /admin 设置管理员」。左栏到此冻结，不再变化。
Scene 3 (5.4–10.6s): 右栏（重头）展开：命令逐字敲出（typing + key-press）——
`python tools/deploy_remote.py \` 换行 `--host <服务器> --user root`。
Scene 4 (10.6–16.0s): 右栏命令下方浮出流程行 `预检 → 上传 → 装依赖 → 配 systemd → 启动验证`，**五个环节依次点亮**（各 0.55s 间隔，ping 落在末级），随后浮出两条前提「服务器需在中国大陆」与「时区 Asia/Shanghai」（后者加 coral 标记）。稳定读完。

## Frame 7 — AI 辅助部署与收尾

- scene: 对话气泡示意把仓库交给 AI，四个自检命令逐条敲入，最后回到片名收尾
- duration: 14s
- poster: 11s
- transition_in: crossfade
- status: outline
- voiceover: "仓库文档齐全，可以直接交给 AI 编程助手来部署。它还带了一套自检脚本：一致性核对、隐私自查、上线前探针。地址在项目主页 —— 请先把免责声明读完。"
- blueprint: compose
- focal: 四个自检命令的终端面板
- roles: 对话气泡 = supporting（先出）· 命令面板 = foreground subject · 收尾片名行 = supporting
- sfx: pop, typing, chime
- src: compositions/frames/09-ai-and-outro.html

首尾呼应。最后一行再次提示先读免责声明，不留"随便用"的印象。
Scene 1 (0.0–2.2s): 非对称 60/40。kicker「也可以交给 AI 来部署」入场，左侧对话气泡「帮我把这个仓库部署到服务器」pop-in。
Scene 2 (2.2–4.6s): 气泡下方说明「仓库文档齐全、命令固定，可直接交给 AI 编程助手。它还带一套自检脚本」淡入。
Scene 3 (4.6–10.6s): 右侧深色代码面板中四条自检命令逐条敲入（typing），每条落定时 click-soft：`verify_all.py` · `check_server_sync.py` · `audit_privacy.py` · `preflight.py --online`。
Scene 4 (10.6–14.0s): 全宽发丝线横贯，线上方收尾：左侧片名 `csuft-auto-checkin`（EB Garamond），右侧 coral 小字「使用前请先读完免责声明」（chime 落在这一行）。全部静止结束。

---

## Video direction

**整体**：技术文档 / 终端编辑风。**暖米色纸张打底**（`code-editorial` 预设的 cream `#FAF9F5`，**不是纯白**），单一陶土色 `#CC785C` 作强调，等宽字承担代码、编号与索引标签。没有任何装饰性插图 —— 每一帧的视觉都是排版、示意图或代码本身。

**深色只用于代码面**：`navy-elev #252320` 的面板仅用于"代码/终端"语义，全片出现三次（帧 1、6、7）。浅色场上的深色代码面是画面里最有分量的元素 —— 这是刻意的对比锚点。

**镜头语法**：七帧用六种不同取景，避免读成同一套模板 ——
① 居中（帧 1）· ② 紧凑栅格（帧 2，3 列）· ③ 全宽四步条带（帧 3）·
④ 左右分屏（帧 4、6）· ⑤ 全宽时间轴条带（帧 5）· ⑥ 非对称 60/40（帧 7）。
相邻两帧不用同一取景。

**动效原则**：全部走"信息依次到位"。每帧 4–5 个 Scene，拍与拍之间留 0.6–1.2 秒阅读停顿；**绝不允许开头一次性铺满然后静止**。入场用淡入 + 轻微位移（8–16px）、`draw`、`layer-reveal`、逐字 `typing`、`pop-in`、**逐级点亮**（帧 5 的流程行）。缓动统一走长尾（`power3` 类），平滑优先于弹跳。不做旋转、不做缩放抖动、不做无意义的呼吸位移。**每帧都以静止读完收尾** —— 宁可安静，不要坏动效。

**转场（不要闪烁）**：全部帧之间用**同底色叠化**（crossfade 0.5s）。因为全片统一暖米底，两帧叠化时不会出现亮度跳变 —— 闪烁正来自深/浅底交叉时的中灰瞬间。**不叠加任何扫光、遮罩或位移动画**，转场期间画面只做亮度交叉。

**颜色纪律**：coral 每帧最多出现两处重点，只用于三类地方 ——
风险提示词（帧 2 的「违纪行为」「纪律处分」）、关键数值（帧 2 的 `0 m`、帧 4 的随机时刻）、当前高亮项（帧 4 的下划线、帧 5 的时区标记、帧 6 的收尾提示）。其余一律墨色与灰阶。**不用红黄警示色**。

**文字与"字幕"**：本片无旁白，所以**解释性文本直接写在帧内并按 Scene 定时揭示**，
不依赖独立的字幕轨（无 TTS 词级时间轴时字幕轨会被合法跳过）。
原则：画面给具体信息（命令、编号、数值、局部标签），帧内文本句给解释性表达；两者不重复堆叠。

**音效**：点缀式，音量约 0.35。转场用 whoosh-short，元素入场用 pop / click-soft，
关键落定用 chime / ping，打字段落用 typing，片名落定用 impact-bass-1。
**不加背景音乐**（本机无 MusicGen，且无声讲解加音乐反而干扰阅读）。

**节奏与配比**：前 41 秒是片名 + 免责声明（帧 1–2），刻意慢、留白多；
中间 16 秒讲接入流程（帧 3）；后 61 秒功能、流程与部署（帧 4–7），
节奏略快、动效更丰富但仍保持文档语气。
这个配比是刻意的：合规内容不该被快进带过，也不该让观众等太久。
