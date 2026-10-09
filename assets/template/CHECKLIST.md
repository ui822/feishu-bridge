# 飞书桥接开通清单

## 0. 开工审计
- [ ] 已确认不是重复部署：现有 bridge 目录、进程、hooks、cron/systemd 没有另一套正在跑。
- [ ] 已确认部署目标目录与当前 Agent 的工作区/hook 机制；非 Muse 环境已准备好自己的 inbox 轮询处理器。
- [ ] 已确认这是中国飞书 `open.feishu.cn`；国际版 Lark 未按本模板实测，先单独验证 API 域名与 SDK 域名。

## 1. 飞书后台（由应用所有者操作）
- [ ] 企业自建应用已创建，机器人能力已开启，用户能在飞书中找到并私聊机器人。
- [ ] 事件订阅选「使用长连接接收事件」，事件含 `im.message.receive_v1`。
- [ ] 权限已申请并生效：`im:message.p2p_msg:readonly`、`im:message:send_as_bot`。
- [ ] 群聊可选：`im:message.group_at_msg:readonly`，并按需在群中实测未 @ 不响应、@ 才响应。
- [ ] 附件需要：`im:resource:upload`（或 `im:resource`），改完权限后按后台要求创建/发布新版本。
- [ ] 应用已发布/可用范围包含测试用户。

## 2. 凭证门禁（启动前必须选定）
- [ ] 已向所有者说明两种模式并得到明确选择，未把讨论当成同意。
- [ ] 模式 A（更稳妥）：凭证仅经 stdin 进监护内存；进程/整机重启后由健康检查提醒所有者补发。
- [ ] 模式 B（全自动恢复，仅限所有者明确接受的低风险/小号机器人）：真实 `credentials.json` 只放运行目录，权限 0600，已 .gitignore；值不进入 skill、代码、日志、命令参数、环境文件或长期记忆。
- [ ] 已拿到 App ID 与 Secret 原文；Secret 只负责地复制一次，随即校验，失败就停，不凭记忆转写、不从聊天记录回捞。

## 3. 本地部署
- [ ] 已运行上层 skill 的 `bin/install_template.sh` 复制模板到运行目录（默认 `~/workspace/feishu-bridge`）。
- [ ] `.venv` 已创建，`requirements.txt` 已安装。
- [ ] `config.json` 只含非秘密字段；`app_id` 正确；白名单先留空等首条真实消息回填。
- [ ] 模式 B 已建 `credentials.json`（0600）；模式 A 不建此文件。
- [ ] 所有 Python 已 `py_compile`，shell 脚本可执行且通过语法检查。

## 4. 启动前冒烟
- [ ] token 换取只报 code/msg/expire；失败即停（常见 invalid 不连续重试）。
- [ ] 机器人信息接口可达；群列表缺权限不阻断私聊验收。

## 5. 保活启动
- [ ] 用 supervisor 启动（模式 A 是 stdin 注入脚本；模式 B 用 `bridge/start_bridge.sh`）。
- [ ] supervisor/daemon 进程存在；daemon 与 supervisor 心跳在 15 秒内刷新。
- [ ] 日志出现 WS 已连上的事实；报告时不复制 WS 地址、ticket、access_key。
- [ ] 若同时跑旧进程，已只停旧进程树，未误杀无关 Python。

## 6. 文字端到端
- [ ] 用户在飞书私聊机器人发一条约定测试文字。
- [ ] 追踪四段：daemon 入队时间、处理器写 outbox 时间、daemon 发送日志、用户飞书端确认收到。
- [ ] 从首条真实消息回填 `allowed_open_ids` 与已验证 `allowed_chat_ids`，再核验配置重读逻辑。
- [ ] 已记录回复延迟量级，不把轮询延迟说成即时原生通道。

## 7. 保活实测
- [ ] 只杀 daemon 子进程（pid 取自 `state/daemon_child.json`），不杀 supervisor。
- [ ] supervisor 在退避后拉起新子进程，pid 改变，WS 再连通；之后再发一条文字或至少核验心跳与连接。

## 8. 自动唤醒与健康恢复
- [ ] inbox hook 每 15 秒：空 inbox dry-run 为 silent；假消息 dry-run 为 wake；假消息已清理后才启用。
- [ ] inbox hook 工作者只写 outbox/processed，不直连飞书 API，不找 Secret。
- [ ] health hook 每 60 秒：健康时 dry-run 为 silent；逻辑已手工追踪陈旧心跳 wake 分支；启用后 30 分钟不重复唤醒。
- [ ] health hook 在模式 B 可运行 `start_bridge.sh` 自动恢复；在模式 A 只通知所有者补发 Secret。
- [ ] 若当前 Agent 没有 hooks，已用等价轮询/任务系统替代，并同样验收空/有消息两种情况。

## 9. 附件验收
- [ ] 已运行 `generate_test_files.py` 生成 txt/png/mp4。
- [ ] 出站：txt 按 file 发送成功、图片按 image 发送成功、mp4 按 media 发送成功；每个都记录 daemon 日志时间。
- [ ] 用户在飞书端分别确认能打开/播放；API code 0 不替代用户端确认。
- [ ] 入站：请用户从飞书新发一张图片和一个小视频，daemon 日志显示对应 `type` 与 `attachments` 数量且 `attachment_errors=0`，inbox JSON 带本地路径，文件落在 `queue/incoming_attachments/<message_id>/` 且大小/类型可核验，工作者能读到本地文件并把回复发回飞书；视频可另核验 ffprobe 时长/编码。
- [ ] 如需补旧消息，可用 `bridge/backfill_message.py --message-id <id>` 先下载校验；补取成功不替代上一项真实新入站事件验收。入站只真实验过视频时，不要声称图片/文件/音频已逐项通过。
- [ ] `state/last_send_error.json` 若存在，先看修改时间；旧错误不能当本次失败。

## 10. 收尾
- [ ] 报告状态矩阵：后台权限、凭证、WS、文字、杀进程恢复、hooks、附件、白名单、整机重启上限。
- [ ] 已说明飞书侧只承接所有者约定的轻量工作；浏览器接管、原生审批、安全录入、付款和重要删除仍回主界面。
- [ ] 已给出停止/回滚方法；未经所有者批准不删凭证、队列证据或应用。
