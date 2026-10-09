# Feishu Bridge

> ⭐ **Support This Project**
>
> [![GitHub stars](https://img.shields.io/github/stars/ui822/feishu-bridge?style=social)](https://github.com/ui822/feishu-bridge)
> [![GitHub forks](https://img.shields.io/github/forks/ui822/feishu-bridge?style=social)](https://github.com/ui822/feishu-bridge)
> [![GitHub issues](https://img.shields.io/github/issues/ui822/feishu-bridge)](https://github.com/ui822/feishu-bridge/issues)
>
> 如果这个项目对你有用，欢迎点个 ⭐ Star！你的支持是我持续更新的动力。

把飞书企业自建应用机器人桥接到 AI Agent 的完整方案：长连接收消息、附件双向收发、进程保活、整机重启恢复。

## 功能

- 长连接监听飞书消息推送（`im.message.receive_v1`），断线自动重连
- **入站附件自动下载到本地**：用户发来的图片/视频/文件落盘到队列目录，直接交给 Agent 分析
- 出站文字与附件发送（文档按 file、图片按 image、视频按 media）
- 进程保活（supervisor）、心跳健康检查、一键幂等启动
- 白名单、消息去重、脱敏日志

## 文件结构

```text
SKILL.md                    # 主 skill：开通顺序、权限矩阵、凭证门禁、验收、排错
assets/template/            # 可直接部署的桥接模板
  bridge/                   # daemon / supervisor / 启动脚本 / 附件补取
  hooks/                    # 健康检查与收件唤醒脚本（Muse hooks 运行时用）
  ops/                      # systemd 服务示例
  CHECKLIST.md              # 从 0 到验收的操作清单
  README.md                 # 模板导航
bin/install_template.sh     # 模板安装脚本
references/full-guide.md    # 完整指南：权限、排错表、停止方法
```

## 快速开始

1. 先读 `SKILL.md`，按里面的清单在飞书开放平台完成机器人配置（机器人能力、私聊范围、长连接订阅、权限、发布版本）。
2. 把 `assets/template/` 复制到运行目录（默认 `~/workspace/feishu-bridge/`），按 `credentials.example.json` / `config.example.json` 填本机配置。
3. 运行 `bridge/start_bridge.sh` 一键启动。
4. 按 `assets/template/CHECKLIST.md` 逐项验收（文字端到端、出站附件、入站附件、保活）。

## 安全

- 真实凭证只放在运行目录的 `credentials.json`（0600 权限），永不进仓库；本仓库所有凭证文件均为模板。
- 日志只输出脱敏信息（code/msg、阶段、时间、pid），不记录凭证与消息原文。

## 实测范围

中国飞书（open.feishu.cn）私聊：文字、出站文档/图片/视频附件、入站图片/视频自动下载。国际版 Lark 与群聊需另行验收。
