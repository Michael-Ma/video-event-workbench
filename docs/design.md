输入一段视频和自然语言 query，自动识别全部匹配事件，确定事件时刻或起止范围，截取对应片段，同时输出中间 state 和 debug messages。任务保持通用，小球落地与机械臂抓取作为首批样例。

当前版本采用本地 Web、单用户、云端视频模型。首批公开或测试素材允许发送云端。默认尽量找全；系统输出模型识别结果与不确定项，不把处理完成等同于找全全部事件。

本版本不包含人工标注调整、阶段标注、片段内容总结或评分、训练数据集工作台、完整评测平台。更新日期为 2026-10-05。

## 输入输出和版本范围

输入只有视频、query 和可选的处理预设。没有逐条人工确认步骤；用户可以查看结果、查看调试信息、修改 query 或配置后重新运行。

| 输入或输出 | 内容 |
| --- | --- |
| 输入视频 | 本地上传文件；保留原片、内容哈希和实际时间戳 |
| 输入 query | 要找的事件、对象及显式包含或排除条件 |
| 处理配置 | 普通动作或短促事件预设、上下文边距、模型、采样与请求预算 |
| 识别结果 | 匹配、排除或不确定；原视频时间、证据帧、来源候选和边界状态 |
| 截取结果 | 已定位的匹配片段；不确定片段分开保存并标明是上下文预览 |
| state | 当前处理阶段、完成窗口、失败范围、候选与输出数量 |
| debug | 结构化操作日志、模型请求参数与响应、时间转换、合并或拒绝理由 |

小球落地默认表示每次可见触地，持续贴地或滚动不重复计数；可通过 query 改成只找首次触地。机械臂抓取默认表示抓取尝试，失败也属于一次尝试；query 明确要求成功时才增加成功条件。预设内容进入本次执行配置，不写成算法中的固定业务分支。

当前只输出自动识别结果，保留 provenance 方便以后使用；不将它们宣称为人工确认的训练真值。

## 最小系统架构

```text
React 输入和结果页
        ↓
FastAPI
        ↓
SQLite 保存 run 与 task 状态
        ↓
一个 Python worker
  ├─ FFmpeg 和 PyAV
  ├─ 一个模型 provider adapter
  └─ 本地结果与调试文件
```

首版使用 Vite、React、TypeScript、FastAPI、SQLite 和本地文件。一个 worker 处理任务，外部调用采用有上限的并发。API 不执行长视频处理。进度使用普通轮询，暂不引入 Redis、Celery、消息总线、向量数据库、多用户权限或多机 worker。

最小接口为上传视频、创建 run、读取 run 和结果、读取增量日志、取消 run，以及读取片段文件。每次 run 冻结原视频引用、query、有效配置和提示词版本。重新运行创建新 run；媒体预处理可复用，模型结果不跨运行无声复用。

前端只有输入区、进度与调试区、只读结果列表和片段播放/下载。没有拖拽边界、确认拒绝、拆分合并等标注操作。

## 从输入到输出的十个步骤

步骤 1 和 2 的媒体产物可以供不同 query 复用；步骤 3 至 10 属于一次具体运行。

| 步骤 | 输入 | 处理与模型 | 输出 |
| --- | --- | --- | --- |
| 1 导入和探测 | 原视频 | 普通代码计算哈希，FFprobe 检查时长、编码、旋转和时间信息 | media_id、原文件、元信息 |
| 2 准备媒体 | 原视频和元信息 | FFmpeg 按需生成可播放代理；PyAV 建立源帧时间索引 | 代理、原帧索引、时间映射 |
| 3 解析 query | 用户原话 | 简单预设或一次文本模型调用，提取事件与边界规则；代码校验 | 精简 QuerySpec、采用的默认值 |
| 4 规划窗口和采样 | QuerySpec、模型能力、RunConfig | 确定每窗负责范围、前后上下文与采样；检查输入限制 | WindowPlan、待处理任务 |
| 5 全片扫描 | 每个窗口的视频或带时间戳帧 | VLM 枚举全部粗候选，允许边界截断、疑似事件和空结果 | 原始 candidates、证据、每窗状态 |
| 6 候选分组 | 全部扫描结果 | 程序标出明显重复或关联候选；保留原记录，歧义不提前删除 | candidate groups |
| 7 局部核实和精定位 | 候选组、query、邻域视频 | 一次 VLM 请求同时判断匹配并细化时间，允许零个、一个或多个事件；必要时有限扩窗 | supported、rejected、unresolved，时间与证据 |
| 8 整理最终事件 | 精定位结果和原候选 | 最终去重、检查边界和冲突；保留来源与未解决项 | matched、uncertain、rejected 结果 |
| 9 截取视频 | 最终结果和原片 | FFmpeg/PyAV 按源帧时间裁剪并校验 | MP4、实际首尾帧、裁片范围 |
| 10 发布结果 | 片段、事件、任务状态与日志 | 普通代码写 results.json 和下载清单 | 完整或部分结果、state、debug |

核实与精定位都是完成识别所需的内部步骤。删除的“片段分析”指额外生成内容总结、技能评价或训练点评；当前模型只返回匹配判定、时间与简短证据理由。

步骤 7 后发现相邻两次事件或跨窗延续时，可回到候选分组或读取邻域；回路有次数和输入范围上限。达到上限仍不能定位就输出不确定，不等待人工介入，也不编造精确边界。

## QuerySpec 与窗口采样计划

QuerySpec 保留 raw_query、event_kind、目标描述、显式匹配和排除条件、occurrence_policy，以及 point 的 anchor_rule 或 interval 的 start_rule/end_rule。较长活动也用 interval 表示，暂不建立复杂活动树。occurrence_policy 默认为 all，可支持全片范围内的 first/last；扫描仍枚举局部候选，在最终去重后按源时间执行全局选择。较早或较晚范围有失败缺口、未解决候选或先后不确定时，不能断言是全片首次或最后一次；复杂的“每组第几次”暂不自动解释。

raw_query 是权威输入。解析不能自行增加“成功”“完整”“仅主角”等筛选条件；未指定的边界采用公开默认值并写入 debug。query 无法解析或要求不可从视频判断的量时返回明确错误或不确定性，不强行执行另一个任务。修改 query 通过新 run 进行。

规划器是普通程序，三个输入分别表示：QuerySpec 决定要观察的时间尺度；模型能力描述单次允许的视频、帧数、分辨率和 token 限制；RunConfig 指定我们选择的窗口、采样、并发和请求上限。同类 query 可以复用相同预设。

| 预设 | 负责区间 | 前后上下文 | 首轮扫描配置 |
| --- | --- | --- | --- |
| 普通动作区间 | 30 秒 | 各 5 秒 | 先试 4 FPS |
| 短促点事件 | 10 秒 | 各 2 秒 | 先试较密采样，例如 12 FPS |

这些是待实测起点，不是最佳参数。query 不直接自由生成任意采样参数，先选择预设，再由规划器校验和必要拆分。一小时普通动作配置产生 120 个负责区间；中间窗口通常读取 40 秒，首尾窗口裁到视频范围内。

假设模型一次最多 128 帧，40 秒乘 4 FPS 需要 160 帧，不能直接提交。可将负责区间缩为 20 秒，加前后上下文共 30 秒，即 120 帧；实际实现还要使用所选接口的 token 和图像尺寸预算估计，保留余量并处理明确的超限错误。不得悄悄降低 FPS 后仍报告原配置。

记录 requested_fps、实际发送的帧 ID、最大观测间隔；原生视频接口不暴露内部采样时记录 actual_sampling_known=false。文件读取到结束与模型看过每一帧是不同事实。

## 关键定位算法

### 全范围候选扫描

所有负责区间都要执行；不以 Top K 检索或场景运动量筛掉剩余视频。每窗提示模型枚举全部候选，保留局部时间、主体与对象、关键证据以及 open_left/open_right。如果输出截断、达到候选数量上限或格式不完整，该窗口不能算成功，应缩小窗口有限重跑或标记 partial。

第一轮重点提高候选召回。识别小球触地时可以利用接触前后的状态变化提出疑似候选；后续密集复看只能检查已发现候选，无法保证挽回全片扫描已经漏掉的事件。debug 必须支持比较两次不同采样配置的 run。

### 分组和精定位

精修前只建立关联，不用单个 IoU 阈值或传递式时间聚类删除候选。局部请求明确说明粗候选可能错误，返回 events 数组；可以拒绝候选、拆成多个相邻事件或标为 unresolved。响应还必须包含 candidate_dispositions，逐一将输入候选 ID 映射到输出事件，或标为 rejected/unresolved。未被响应覆盖的输入候选自动保持 unresolved；events 为空不能直接将整组判为 rejected。

精修后再去重，依据同一主体与对象、同一事件证据、锚点或边界的一致性。窗口负责区间只决定主要来源，不能因为候选落在上下文区就删除它：其相邻负责窗口可能漏检。无法判断是否同一事件时保留冲突组，输出 uncertain，不能强行合成一个。

### 跨窗口事件

open_left/open_right 触发邻域复看。窗口长度不是事件最大长度；长事件可以根据连续证据向前后追踪，再在两端局部定位。读取失败、对象身份冲突或达到扩展预算时保留开放边界，不跨不可见区间拼出确定事件。

任务可以连续重复，不能硬设一个适用于所有事件的最小间隔。小球连续反弹和机械臂连续抓取都必须保留为可能独立的实例。

## 时间轴和裁片规则

原视频时间为唯一依据。业务存储使用相对第一展示帧的整数微秒，同时保留原始 PTS、time base 和帧序号；微秒是存储单位，不是模型精度。代理、模型窗口和导出片段各自的局部时间需要显式映射，不使用帧号除以标称 FPS 替代变帧率时间。

统一使用半开区间 [start_us, end_us)。每个实际提交的 input_asset 保存局部展示时间到源帧的映射，不直接把请求的裁剪起点当作实际输入零点。模型返回局部秒数或本请求 frame manifest 中的帧 ID，由适配器转换；帧证据和来源候选 ID 必须真实存在。原生视频接口不能提供确切帧时，仅保存证据时间范围与引用粒度，不伪造原帧引用。越界、倒置或无法映射的结果不能直接用于裁片。

point 输出接触锚点及其可接受范围，裁片范围由锚点上下文配置生成，默认前后各 1 秒。interval 输出 start/end 及 start_range/end_range，按配置增加展示边距；不确定边界可使用其外包范围保留上下文。范围缺失不能当作零误差；保留估计、未知原因和实际 cut_range，边界容差属于运行实验配置，不表示已实现的准确率。

result_bucket 由代码计算：decision=supported、时间有效且边界闭合、边界不确定范围符合本次容差、无未解决实例冲突，才进入 matched。bounded 仅表示有限位置，不单独证明边界可靠。其余未解决候选进入 uncertain；可导出已知观察窗口作为诊断预览，并标记 context_fallback。无法确定合法范围时只输出记录。rejected 保留 debug，不生成正式片段。裁片失败另记 clip_status=failed，不能把语义匹配改为 rejected。

裁片从前一个可定位点开始解码，再选择目标帧并转码，记录实际首帧 PTS 与末帧结束时间。stream copy 不作为精确边界默认路径。[FFmpeg 的 seek 行为](https://ffmpeg.org/ffmpeg.html)

## 结果格式与中间 state

识别结论和边界状态分开：decision 为 supported、rejected、unresolved；boundary_status 为 bounded、open、unknown；最终结果分类为 matched、uncertain、rejected。confirmed 或人工真值不是本版本的状态。

以下数字仅说明输出结构：

```json
{
  "event_id": "event_007",
  "source_candidate_ids": ["window_17_a", "window_18_b"],
  "decision": "supported",
  "boundary_status": "bounded",
  "result_bucket": "matched",
  "location": {
    "kind": "interval",
    "start_us": 512180000,
    "end_us": 518420000,
    "start_range_us": [512100000, 512300000],
    "end_range_us": [518300000, 518600000]
  },
  "evidence_refs": ["source_frame_15365", "source_frame_15552"],
  "clip": {
    "path": "clips/matched/event_007.mp4",
    "cut_range_us": [511680000, 518920000],
    "kind": "event_with_context"
  }
}
```

point 的 location 改为 anchor_us 和 anchor_range_us，不伪装成零长度 interval。未知时间使用 null。所有结果带 media_id、run_id、query/config/model 版本以及源时间映射；模型分数只作原始信息，不显示为校准概率。

run 状态为 queued、running、completed、partial、failed、cancelled。stage 记录当前步骤；stop_reason 记录请求上限、外部调用未知或其他失败。completed 表示必需处理和输出步骤结束，可以仍有 uncertain；partial 表示存在失败、未扫描或未完成输出范围。

state 至少包含扫描完成范围与缺口、实际采样可观测性、候选数、复看完成数、matched/uncertain/rejected 数、裁片成功与失败数。scan_complete 只按当前有效 WindowPlan 中成功任务的 core_range 并集判断；read_range 仅是上下文，不能代替失败窗口的负责范围。缩窗重跑记录父任务与替代子任务，按叶子负责区间计算覆盖，不重复累加。裁片失败使运行 partial，即使扫描已经完成。scan_complete 不等于 all_occurrences_found，系统不自动声称后者。

## debug 内容与最小持久化

SQLite 保存 media、run、task 和结果索引；大文件及请求响应放本地 run 目录。输入、配置与最终结果可追溯即可，暂不建立人工 revision、DatasetSnapshot、GoldSnapshot 或多层审核实体。

```text
runs/run_001/
  input.json
  window_plan.json
  scan/
  refine/
  results.json
  debug.jsonl
  clips/matched/
  clips/uncertain/
```

每条 debug message 包含 seq、time、stage、task_id、level、code、message 和 artifact_refs。message 记录可检查的处理动作和证据，例如“窗口 17 返回 3 个候选”“候选 A/B 精修后判定为两次事件”“局部时间 7.0 秒映射为源时间 512.0 秒”。

保存模型名、提示词版本、请求范围、有效采样配置、结构化响应、校验错误、去重关系、重试和实际用量。不要要求模型输出思维链，不把长篇自由解释作为 debug；凭据和授权请求头不入日志。

用户能按 run、窗口或事件查看中间结果。缺候选、明确排除、语义不确定、请求失败、裁片失败分别显示，不全部归结为“没有找到”。

## 失败重跑与取消

首版一个 worker，以单实例锁避免同时启动两个处理进程。任务用 SQLite 记录 pending/running/succeeded/failed/interrupted/request_unknown/cancelled。发送模型请求前先事务保存 attempt_id、request_intent 和 submitting 状态，收到响应先持久化再提交任务完成状态。重启后未结算 intent 标为 request_unknown；本地解码和裁片可以重做，可能已提交的外部请求不盲目重发。

一个任务同时只有一个有效 attempt。输出路径按 attempt 隔离，提交时核验当前 attempt_id；旧 attempt 不能覆盖当前文件或结果。重复点击使用请求幂等键；主动重跑创建新 run 并保留旧结果。

调用设置超时、并发、请求数和输出上限。已知未提交的暂时错误有限重试；输出截断或输入超限优先调整窗口并记录新计划。外部已提交但响应丢失的任务不自动无限重发；保留已有部分结果和问题说明。

取消后停止新任务，尽力终止本地媒体进程；在途模型调用可能仍返回并产生费用，只归档，不复活已取消运行。没有任何 matched 事件也可以正常完成，但必须区分真正完成的空结果与窗口处理缺口。

## 算法依据与需要验证的假设

已有研究足以支持先构建分层定位原型，不能据此承诺任意一小时视频无漏检。

| 设计选择 | 已有依据 | 本项目仍需验证 |
| --- | --- | --- |
| 分范围扫描后局部定位 | 小时级视频研究将搜索与局部定位分开后取得改善 | 该研究多为单目标查询；我们要枚举全部重复实例 |
| 返回多个区间并单独处理重复次数 | OMTG 和 TimeLens2 研究多实例时间定位 | 不同机位与机器人任务的全事件召回和合并错误 |
| 显式控制采样与输入范围 | Gemini 官方视频接口支持相关配置，默认低频采样可能漏快动作 | 短触地事件在原视频和所用接口中是否可见 |
| 核实与精定位合成一次调用 | 两个输出可由一次结构化局部请求表达 | 与分两次调用相比的准确性、调用数和失败率 |
| 规则负责时间映射和裁片 | 视频工具提供确定的解码与时间处理接口 | 变帧率、旋转与边界帧的实际一致性 |

来源：[ExtremeWhenBench](https://arxiv.org/abs/2606.12300)、[OMTG](https://arxiv.org/abs/2606.06294)、[TimeLens2](https://github.com/MCG-NJU/TimeLens2)、[Gemini 视频处理](https://ai.google.dev/gemini-api/docs/video-understanding)。

首版先使用一个 Gemini Flash 配置：文本调用解析 query，视频调用执行 propose 和 verify_refine。接口采用 analyze(operation, request)；不需要预先接入多个供应商、姿态模型、检测器或 embedding 服务。机器人 ER 模型、TimeLens2 和专用检测器是后续对照选项，只有在失败案例指向对应瓶颈时加入。

## 关键情况推演

| 情况 | 必须得到的行为 |
| --- | --- |
| 同一次触地出现在相邻两个窗口 | 保留两份证据，精修后产生一个事件及 duplicate_of 关系 |
| 两次触地很接近 | 不按固定时间阈值合并；局部请求允许返回两个事件 |
| 抓取跨越窗口边界 | 用重叠上下文提出候选，open 边界触发复看 |
| 活动持续超过一个窗口 | 保存延续关系并追踪两端；不以窗口长度截断活动 |
| 一个扫描窗口请求失败 | 最终 partial，给出源时间缺口；其他窗口结果仍可输出 |
| 模型生成合法 JSON 但时间越界 | 校验拒绝或有限重试，不裁出错误片段 |
| 模型判断事件存在但看不到完整边界 | uncertain 或 context_fallback 预览，不冒充完整事件 |
| 初扫漏掉一次短事件 | 局部复看无法补救；改变扫描密度或策略后以新 run 对照 |
| 视频没有任何匹配 | 完成的空 results 与失败状态严格区分 |
| 导出精确、识别却错误 | 保留两层来源；裁片正确不能证明语义匹配正确 |

推演检查的是算法与状态逻辑。真实效果仍须在小球落地和机械臂抓取素材上运行，重点观察漏检、相邻事件合并、跨窗边界、提示词歧义和原始时间误差。不要继续扩大模型清单来替代这轮实验。

## 实现顺序

先完成视频导入、时间索引、run 状态与日志，再接一个真实模型跑通全片候选扫描；随后实现局部核实和精定位、最终去重、裁片与结果发布。首版用同一套代码处理两种样例，变化仅来自 query 与配置。

开发时保留小范围的确定性自检：窗口范围无遗漏、采样不超限、局部到源时间转换正确、合法区间裁片、失败范围可见、结果不被旧任务覆盖。正式质量门槛、人工标注产品、训练导出格式和完整评测平台以后再做。



## 实现决策 2026 10 05

私有仓库为 [video event workbench](https://github.com/Michael-Ma/video-event-workbench)。首版采用 React 和 TypeScript 前端、FastAPI 接口、单独 Python worker、SQLite、PyAV 和 FFmpeg。共享契约完成后，前端、媒体处理、识别流水线按依赖并行；集成和验证统一进行。

1. **状态与日志存储**：使用 Python 自带 SQLite 保存 media、run、task、增量日志和 worker 心跳，替代额外 ORM。原视频、帧索引、采样图像、请求响应和片段放在本地数据目录。API key、视频及运行产物不进入 Git。

2. **模型输入**：首个 Gemini 适配器提交显式带原视频时间戳的 JPEG 帧序列，避免依赖不可见的原生视频采样策略。全片扫描与局部核实均记录实际送入帧、输入起点、采样间隔、模型版本、提示词版本和返回数据。这能审计输入与时间转换；短促事件仍可能发生在采样帧之间。

3. **无 key 流程测试**：提供一个明确标为确定性测试的 12 秒合成视频及 fixture provider，只允许处理内置示例。标签驱动的结果用于验证状态、窗口覆盖、候选核实、裁片和 UI，不能当作模型识别准确率。

4. **时间与裁片**：原视频零点为第一帧显示 PTS；索引保留每帧 PTS、time base 与显示时长。采样不复制帧补齐 FPS，超输入上限需要缩小窗口。裁片选取显示起点位于半开区间内的源帧，解码重编码后核对帧数及帧时间。导航预览单独保留到原视频的映射。

5. **媒体边界**：首版导航预览与导出片段只包含视频，原始上传保留音轨；音频事件识别与音轨对齐留待扩展。旋转支持 0、90、180、270 度。默认单文件上限 2 GB，可配置。

6. **运行与恢复**：接口、worker 和前端可一条命令一起启动；worker 单实例。重复提交使用本地幂等标识。取消后迟到的状态更新不能恢复任务。提交给模型的请求关闭 SDK 隐式重试，远端结果不明时保留 request_unknown，避免重复收费。

7. **结果和权限**：网页提供输入、只读过程状态、窗口覆盖与缺口、匹配和不确定项、片段播放下载、调试产物。API key 仅配置在本地后端，网页只获得是否已配置；文件接口限制在媒体与运行产物目录。

这些实现选择不改变原始 query 的优先级、不添加成功抓取等隐藏筛选条件，也不加入人工标注、阶段分析、内容总结或训练数据平台。

## 集成补充与验证 2026 10 05

真实模型的精定位结果增加观测精度下界：模型给出的边界不确定范围至少覆盖相邻送入帧形成的时间括区，不允许仅靠模型声明零误差获得高于采样密度的精度。确定性 fixture 标签可明确绕过这一限制，用于工程测试。

同一不可变上传可复用已准备的帧索引和预览，复用前核对源文件签名与产物。导航预览除了帧数，还核对与源帧的时间差不超过 2 微秒。全局最后一次按事件开始时刻排序；仅接触半开区间边界的相邻活动不会被当作重复冲突。精定位输出虽符合 JSON schema，但若时间或候选映射不合法，任务仍标为失败。

未知模型请求或调用预算耗尽后，后续窗口保留任务状态和覆盖缺口，停止新增模型提交及无用采样。取消后的迟到结果只能归档，不能发布。页面显示配置、窗口、请求响应及导出文件的可检查引用。

本轮验证通过 54 项后端测试、4 项前端测试、Python lint 及 TypeScript 和 Vite 构建。真实 FFmpeg 集成测试覆盖跨三个负责窗口的区间事件、全局第一次和最后一次时刻事件，并解码核对导出帧数。浏览器确认导入示例、取消、历史恢复、点事件和区间事件、原视频定位、片段播放、调试引用，以及预算中断后 0 到 12 秒缺口的显示；390 像素布局没有横向溢出。

目前已达到接入 API key 和真实测试视频的阶段。未执行付费模型调用，确定性示例不能证明真实识别率、边界准确率或长视频性能。完整测试视频上的召回、短促事件、遮挡、模型实际接受输入、耗时与成本仍需下一轮测量。

## 一键启动脚本补充 2026 10 05

新增仓库根目录的 start.sh。它从任意当前目录定位项目，安装缺少的 uv 和 Node 到被 Git 忽略的 .tools 目录，用 uv 准备 Python 3.13 与锁定的后端依赖，并在前端依赖清单变化时执行 npm ci。缺少 FFmpeg 或 FFprobe 时，macOS 使用 Homebrew，Debian 或 Ubuntu 使用 apt；macOS 若没有 Homebrew，先运行官方安装程序，系统安装可能需要管理员密码。其他 Linux 发行版需先提供 FFmpeg。下载 Node 时校验官方 SHA256 清单。

脚本只在 .env 缺失时从模板创建，权限为 0600，保留已有配置。已导出的环境变量优先于文件值。检查 GEMINI_API_KEY 的配置状态、GEMINI_MODEL、VEW_DATA_DIR、VEW_MAX_UPLOAD_MB、数据目录写权限、视频编码器及本地端口。密钥不打印、不传给前端进程；检查不进行模型调用。默认无 key 时允许内置示例，`--require-api-key` 模式会停止；`--check` 模式只安装和检查。

启动脚本等待 API、worker 心跳与前端都就绪才给出服务地址。使用进程组管理三个服务及其子进程；Ctrl+C、启动超时或任一服务异常退出会停止本次启动的全部服务。端口冲突给出错误，不终止其他已占用端口的程序。

本机实际验证了一次依赖安装、多次依赖复用、配置检查、服务就绪、Ctrl+C 后两端口释放及重新启动，也验证了重复启动和缺 key 的严格模式失败。新增 8 项启动测试，后端共 62 项测试通过，Python lint 和 Bash 语法检查通过。缺少 uv、Node 或 FFmpeg 的全新机器安装分支尚未实际执行。

## 部分完成诊断与界面调整 2026 10 05

这次 54.8 秒测试视频的查询解析完成，首个全片扫描请求在 121.2 秒后没有明确返回；配置超时为 120 秒。后一个窗口未提交，实际成功扫描覆盖和事件数均为零。旧适配器没有保存异常类型，因此只能把超时或连接中断标为可能原因，不能从旧记录确认具体底层异常。

API 增加可见诊断：停在哪一步、失败或未提交的窗口、等待时长、超时配置、影响范围、处理建议与原始记录链接。旧记录在读取时解释，不改写历史日志。新模型失败保存异常类型、时长、帧数及输入体积，避免记录可能包含密钥的 SDK 异常文本；未知请求仍不自动重试。

处理中按扫描、核实和裁片进度发布结果快照。尚未完成全局整理的中间事件明确标为不确定；最终整理后逐个发布片段。取消或失败保留最后已发布的结果。没有事件时直接说明没有可用结果，已有结果提供快捷跳转入口。

上传区域实现真正的拖拽接收、拖入高亮、上传中反馈和单视频校验，与文件选择器共用上传流程。工作区顶部增加整体状态框、阶段步骤、窗口进度、事件与片段数量、扫描覆盖和运行动效。开始前为灰色，进行中为蓝色，成功为绿色，部分完成为琥珀色，失败为红色，取消为灰色；文字和图标同时标明状态。取消按钮改为红色实心按钮，窄屏铺满可点击宽度。开始或选择运行时滚动到状态框，并尊重减少动效设置。

本轮 68 项后端测试、10 项前端测试、代码检查与前端构建通过。浏览器验证了实际历史错误的显示、文件选择器上传、工程测试任务的排队动效与取消、完成后两个片段播放，以及 390 像素布局。拖拽通过 DOM 文件拖拽事件测试，浏览器验证了共用上传路径。未重发旧的未知模型请求，验证期间没有新增付费模型生成。

## Gemini 调用费用记录 2026 10 05

每次 Gemini 调用记录 operation、task 和 attempt 标识、输入帧数及体积、请求时长、原始 token usage 和 estimated_usd。费用记录进入 SQLite task、调试日志及 response 或 error 文件，现有界面的处理记录可查看。提交前冻结本次价格版本和单价，复用已保存响应不重复记账。

当前价格表覆盖 gemini-3.8-flash 的付费 Standard 单价。截至 2026 年末，每百万 input tokens 为 0.75 美元，cached input 为 0.075 美元，output 含 thinking 为 3.75 美元；官方公布 2027 年起分别为 1.50、0.15 和 7.50 美元。计算时从 input 中扣除 cached tokens，再按缓存价计入；output 与 thinking 相加。价格依据为 [Gemini 官方价格表](https://ai.google.dev/gemini-api/docs/pricing)。这是标准价估算，免费额度、折扣和实际账单可能不同。

未知请求结果、缺失 usage、没有已核实单价的模型或尚未覆盖的工具费用均记为费用未知，不能记成零。收到响应后即使 JSON 无效、输出截断或任务已取消，仍保存已有 usage 和费用。Fixture 是本地流程测试，明确记为零费用。

84 项后端测试及代码检查通过，包含缓存扣费、thinking、价格生效日期、未知费用、响应复用、截断和取消后的保存。实际服务完成一次 Fixture 流程，5 次本地调用分别记录零费用，API 返回费用字段和日志。验证未新增付费 Gemini 请求；尚无本任务的多图或原生视频 latency 基准。

## 图片和视频输入以及并行处理 2026 10 05

propose 和 verify_refine 分别支持 images 或 video，UI 可独立选择两步输入方式并保留混合组合。最近运行显示输入组合，当前运行展示冻结配置。images 使用带 frame ID 与实际源时间的 JPEG；video 使用本地裁切、归零并保留源 PTS 映射的 MP4 窗口，不传音频。native video 的证据使用局部时间戳并换算成源时间引用，服务器侧采样不冒充已观察的 JPEG。

默认 action scan 降为 2 FPS，point scan 为 6 FPS，refine 为 6 FPS，单次请求超时为 1200 秒。真实重测出现 MAX_TOKENS：约 7860 个 thinking tokens 挤占 8192 的输出预算，导致 JSON 截断。因此默认改为 thinking low、输出上限 16384、temperature 1，并在高级参数中允许调整。temperature 1 沿用 [Gemini 官方建议](https://ai.google.dev/gemini-api/docs/troubleshooting)；该建议不能证明此前失败由 temperature 引起。

query 解析与媒体准备同时执行；独立 scan windows、refine groups 以及 clip exports 各自并行，默认 model 和 clip 并行数均为 2。提交意图与请求预算原子保存，整个 run 的模型请求上限由共享信号量约束。未知请求一出现即阻止新付费调用，已提交请求继续保存响应与费用；日志与进度快照按顺序写入。全局 grouping 和 occurrence 筛选仍遵守阶段依赖。

跨窗口的 entity_key 可能被命名为 shooter 或 player，不能据此认为是不同的人。跨窗口时间重叠的 proposals 可一起 refine，保持 complete-link 与输入范围上限；所有 proposals 保留，模型仍可返回零个、一个或多个 event，不强制合并。测试中 33 秒附近的重复 event 已消除，并保留两个来源 candidate。rejected 或 unresolved disposition 如显式关联同判定的 event，兼容其关联并保留原 matching decision；矛盾的关联仍拒绝。图片测试的此类格式问题使用已保存响应重新解析，未增加模型调用，原始响应与旧结果快照保留。

native video 局部片段曾因时长元信息少约 0.667 毫秒被误判失败。现在仅在明确的 MP4 movie timescale 支持该量化误差时接受；帧数、逐帧 PTS 与解码末帧仍校验到 2 微秒，源 origin 和范围不按毫秒取整。量化依据与误差写入 duration_validation。

UI 展示每次调用的 input mode、latency、input/cached/output/thinking tokens、估算费用与价格版本，并单列已知小计、未知及在途数量。clip 的本地 attempt 不混入模型费用；取消后仍接收晚到费用。处理时间和排队时间分开显示。

同一段 54.8 秒 IMG_0228.MOV 和原 query 的最终测试均完成。以下处理时间排除排队与人工排查等待，费用按已保存 Standard 价格估算：

| 输入组合 | 处理时间 | 模型调用 | 自动判定 | 导出片段 | 估算 USD |
| --- | --- | --- | --- | --- | --- |
| images 到 images | 66.8 秒 | 9 | 5 matched 和 1 rejected | 5 | 0.335336 |
| video 到 video | 47.7 秒 | 9 | 6 matched | 6 | 0.039614 |

两者对最后一次投篮的位置条件判断不同，不能仅由数量确定准确率。总共 6 次真实试跑、57 次模型调用的已知标准价估算为 1.553354 美元，包含排查时的截断响应；这不是账户账单。116 项后端测试、33 项前端测试、前端构建与代码检查通过，覆盖四种输入组合、并行限额、原子预算、未知结果阻断、取消与费用保存、fractional FPS 时长量化及 mapping 兼容。实际页面验证了两个输入选择、真实费用明细、390 像素布局和可播放片段。


## English and Chinese interface on 2026-10-06

The header language selector supports English and Chinese, follows the browser language
on first use, and remembers the choice. It translates system labels, run status, configuration,
costs, filters, and known errors without rewriting draft queries, event selection, user
filenames, model judgments, or raw logs. Query templates use the chosen language only when clicked.

Each API request carries Accept-Language. Supported variants and quality weights choose
read-time diagnostic and error copy; API defaults remain Chinese. Content-Language and
Vary describe the returned language. Stable error codes and structured parameters let
existing errors change language while preserving technical details.

Validation passed 142 backend tests, 56 frontend tests, Ruff, and TypeScript/Vite build.
The actual desktop and 390-pixel UI confirmed language changes, saved preference after
reload, unchanged queries, selection and costs, and no horizontal page overflow.
No Gemini calls were made for this localization change.
