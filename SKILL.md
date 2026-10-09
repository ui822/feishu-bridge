---
name: "feishu_bridge"
description: "把飞书/Lark 企业自建应用机器人桥接到 AI Agent：长连接收消息、文件队列、自动唤醒、进程保活、整机重启恢复、文字与文档/图片/视频附件双向收发。当用户要求新接、重连、续命、修复、升级或验收飞书机器人通道，或让另一个 Agent 按已跑通的流程直接上手飞书桥接时使用。已实测范围为中国飞书 open.feishu.cn 的私聊文字、出站文档/图片/视频附件与入站图片/视频自动下载到本地供 Agent 分析；国际版 Lark 和群聊需另行验收。"
metadata: { "includeInPrompt": true }
---

# Feishu Bridge

## Purpose

把一个飞书企业自建应用机器人接到当前 Agent，让用户能在飞书私聊里发文字和图片/视频/文件、交代约定的轻量任务，并接收文字、文档、图片和视频附件。入站附件会被 daemon 自动下载到本地队列目录，交给 Agent 读取和分析，而不是只停留在一句“收到了”。核心不是“调通一个接口”，而是把后台权限、凭证、长连接监听、消息队列、Agent 唤醒、进程保活和逐项验收接成一条可复现的流水线。

## Tooling

运行资产在本 skill 的 `assets/template/`，详细说明在 `references/full-guide.md`：

- 一键复制模板：`bin/install_template.sh [目标目录] [--install-deps]`
  默认部署到 `~/workspace/feishu-bridge`；只复制 `*.example`，不会生成真实 `credentials.json`，不会覆盖已有 `config.json`。
- 模板进程：`assets/template/bridge/bridge_daemon.py`（收消息并下载入站附件、发消息/附件、重连、心跳）、`backfill_message.py`（补取已送达消息的入站附件）、`supervisor.py`（子进程保活）、`start_bridge.sh`（幂等启动）、`validate_credentials.py`（只输出脱敏校验结果）、`generate_test_files.py`（生成 txt/png/mp4）
- 模板钩子：`assets/template/hooks/feishu_inbox.sh` 与 `feishu_health.sh`，工作者原文见 `assets/template/hooks/prompts.md`。这是 Muse hooks 形态；其他 Agent 必须用等价轮询/任务机制实现同一 inbox/outbox 合同。
- 部署清单：复制资产里的 `CHECKLIST.md` 跟着逐项打勾，不要凭记忆跳步。

## Auth

- 所需凭证仅一对：App ID（`cli_` 开头，可明文）和 App Secret（秘密）。此 skill 不收集、不保存、不分发真实凭证，也禁止把真实值写进 `SKILL.md`、模板、代码、日志、记忆、命令参数、systemd unit 或环境文件。
- 动手前向应用所有者问清并记录凭证模式，得到明确选择后再继续：
  - `memory_only`（优先推荐）：Secret 只经 stdin 进入 supervisor 内存，子进程自动复活不需补给；supervisor 或整机重启后必须由所有者补发再启动。
  - `local_file`（仅在所有者明确接受、机器人低风险时）：真实 `credentials.json` 放在运行目录，权限 0600，进 `.gitignore`，供 supervisor 和整机恢复自读。不得替所有者默认选择，也不得拿小号经验套到高价值生产应用。
  - 所有者自己的服务器代管 Secret 属于另一架构，本模板未实现，不得声称已支持。
- Secret 只复制一次并立刻跑 `validate_credentials.py` 或等价 token 换取。成功只报 code/expire；失败立即停掉所有自动重试，报准确的飞书 code/msg，并请所有者把 Secret 单独一行重发。禁止凭记忆转写，禁止从聊天记录回捞凭证，禁止边猜边重启。

## Operating Rules

### 1. 先审计再动手

检查目标目录是否已有另一套桥、是否有 supervisor/daemon 在跑、hooks/cron/systemd 是否已注册、队列是否有未处理消息。同一机器人只能有一套消费者，不要在旧桥活着时再启第二套。先读当前状态，再决定是复用、修复还是按清单重装。

### 2. 飞书后台是所有者的动作

向所有者给出明确清单并等待其在后台完成，不要假装已开通：机器人能力、私聊可用范围、长连接订阅 `im.message.receive_v1`、权限 `im:message.p2p_msg:readonly` 与 `im:message:send_as_bot`、出站附件权限 `im:resource:upload`（或 `im:resource`）、按后台要求发布新版本。入站附件下载走消息资源接口，本机实测现有私聊只读权限组合即可下载；若资源接口报缺权限，再补 `im:message:readonly`（或后台当时要求的等价只读权限）并重新发布后复测，不要先入为主地反复改代码。群聊再加 `im:message.group_at_msg:readonly` 并真验收。本 skill 的已验证形态是中国飞书；国际版 Lark 需要先验证 API/SDK 域名后单独跑通。

### 3. 部署与启动

1. 运行 `bin/install_template.sh`，将 `config.example.json` 落成运行目录的 `config.json`，只填写 `app_id`、空的白名单等非秘密字段。
2. 若选 `local_file`，从 `credentials.example.json` 建运行目录的 `credentials.json`，填入所有者提供的一对值后 `chmod 600`；若选 `memory_only`，不要生成这个文件。
3. 编译检查全部桥接 Python，向虚拟环境安装 `requirements.txt`，确认 `import lark_oapi` 成功。
4. 校验凭证成功后才用 supervisor 启动：`local_file` 跑 `bridge/start_bridge.sh`；`memory_only` 由启动脚本从 stdin 喂 supervisor。看进程、双心跳和 WS 连通证据。WS 地址带着 ticket，只准报“已连上”，不准摘抄地址和 key。

### 4. 验收按证据关口逐个关

没有实际证据不得打勾，也不得跳关：

- **文字端到端**：请用户在飞书私聊机器人发一条约定测试语，四段都要对上：daemon 入队时间、处理器写 outbox 时间、daemon 发送 code 0、用户飞书端亲眼确认。从首条真实消息把用户 `open_id` 与已验证的 `chat_id` 回填到白名单后，再验一条。
- **附件**：出站先生成三个小样，再逐个发：普通文件按 file、图片按 image、mp4 按 media；每次都看 daemon 发送日志，用户最后分别确认能打开图片和播放视频。旧的 `last_send_error.json` 先看修改时间再下结论。入站请用户从飞书新发一张图片和一个小视频逐项验：daemon 日志应显示对应 `type` 与 `attachments` 数量且 `attachment_errors=0`，inbox JSON 应带本地附件路径，文件真实落在 `queue/incoming_attachments/<message_id>/` 且大小/类型可核验，工作者能读到本地文件并把回复发回飞书。`backfill_message.py` 补到旧消息只证明消息查询与资源下载链可用，不能替代这项真实新入站事件验收。本机已于 2026-10-09 完成这项真实验收：修复后新发的一条 19 秒视频自动下载、工作者处理并回复成功。
- **保活**：只杀 `state/daemon_child.json` 记着的 daemon 子进程，supervisor 必须退避后拉起新进程（pid 变化、WS 再连）。不要杀 supervisor 来证明子进程保活。
- **整机恢复**：再验健康机制。Muse 环境用模板体检钩子：心跳新鲜时 dry-run 保持安静，陈旧时走读 wake 分支后启用；钩子在 `local_file` 模式只跑 `start_bridge.sh`，在 `memory_only` 模式只通知所有者补发 Secret。其他 Agent 环境要用 cron/systemd 等真正会到点的机制替换，并验证一次真实触发，不要拿写了计划当已复活。
- **群聊**：只有用户要求时才做。未 @ 不入队、@ 后入队并回复，两项都要真发消息验证；没测过就明说未测。

### 5. 飞书侧边界

这条桥适合文字聊天、用户从飞书发来图片/视频/文件供 Agent 分析、约定的轻量编辑和回传成品附件。浏览器画面、浏览器接管、Muse 主界面的结构化审批、安全录入卡和钱包确认无法搬到飞书；凡是付款、向外发信/发布、重要删除、高价值账号设置，不允许只凭一句飞书“确认”就执行，要停下让用户回主界面完成。用户的轻量工作范围由用户自己的指令确定，桥接工作者不要替用户放大。

### 6. 日志与排错

只报脱敏事实：飞书 code/msg、阶段（upload/download/send）、带时区时间、进程 pid、心跳新鲜度。常见情况：99991672 是缺权限，先补后台权限并发布新版本；10014/1000040345 是凭证无效，停重试并请所有者单独一行重发；230055 是 MP4 错用了 file 消息，改 media；用户发了视频却毫无反应，先看 daemon 日志是否出现 `inbox ignored empty/non-text`——出现即说明跑的是旧版纯文字入站，必须升级 daemon 而不是让用户重发；`SSL: WRONG_VERSION_NUMBER` 先分开查 REST 与 WS 及代理发现。完整细节看 `references/full-guide.md` 的排错表。

### 7. 交付报告

收尾时给状态矩阵，不只说“好了”：飞书后台权限、凭证模式、进程与心跳、文字端到端、白名单、保活杀子进程实测、健康恢复、出站文档/图片/视频附件、入站附件真实新消息验收、群聊是否已测，以及仍存在的上限（例如整机恢复是否需要补 Secret、入站资源大小上限）。每项都写证据来源（哪次日志、哪次用户确认），不要把 API code 0 当成用户端收到的替代证据，也不要把 backfill 补取成功说成真实入站事件已通过。

## Workflow

1. 审计现有部署 → 2. 向所有者确认凭证模式与飞书后台工作 → 3. 安装模板并过凭证校验 → 4. supervisor 启动并核连接 → 5. 逐项完成文字、附件、保活、健康恢复验收 → 6. 用状态矩阵交付，附上回到这里时该看哪份文件。完整细节在 `references/full-guide.md`，逐步打勾表在 `assets/template/CHECKLIST.md`。

## Output Contract

每次用完这个 skill，至少交代：当前飞书机器人是谁、跑在哪台机器/运行目录、凭证采用哪种模式（不给值）、到目前为止哪几关已实际通过、哪几关未测或正被什么堵住、下一步需要用户在飞书或后台做的具体动作（如果还有）。
