# 向 Human-Agent-Society/reef 提 PR 指南

> 来源：CONTRIBUTING.md、.github/PULL_REQUEST_TEMPLATE.md、.github/MAINTAINER.md、.github/workflows/ci.yml

## 流程总览

1. **搜重**：先搜上游 issues/PRs 确认无人做
2. **定级**：判断是否需 RFC（见下）；bug 先开带复现的 issue
3. **开发**：fork 分支开发，diff 最小，遵守 AGENTS.md 设计规则
4. **本地验证**：全部真实运行并记录结果
5. **开 PR**：没准备好开 Draft；按模板填 6 节
6. **自动流程**：merge oncall 自动 assign；labeler 自动打 area 标签
7. **合并前**：全量 CI 矩阵全绿 → maintainer squash 合并

## 什么情况需要 RFC（先开 RFC issue 等接受，CONTRIBUTING.md:91-111）

- 新增 reef/ 顶层包、新增共享训练后端、改 recipe 扩展契约
- 重大/不兼容的公开接口变更；改持久化格式/wire 契约/信任边界
- **不需要**：小 bug 修复、文档、测试、保行为的内部重构
- RFC 就是 issue（模板 rfc.yml），不要在 docs/rfcs/ 加文档

## PR 模板 6 节

1. Motivation（链接 issue，`Closes #N`）
2. Changes
3. Compatibility and operational impact（无影响写 None）
4. Verification（**实际跑过的**命令及结果）
5. AI assistance（写 None 或披露工具与用途——必须披露非平凡 AI 辅助）
6. Checklist（含 README 双语同步 + README.i18n.yaml）

## CI 检查与本地预跑

```bash
pre-commit run --all-files        # lint + 设计检查（禁 Protocol/TYPE_CHECKING/assert/del）+ README i18n
.venv/bin/python -m mypy
.venv/bin/python -m pytest tests/ # 全量需训练依赖；可先跑聚焦 suite
```

- Draft PR 只跑轻量检查；ready 后自动跑 Python 3.12 全套
- **合并前必须**全量 3.10/3.11/3.12 矩阵：有写权限者 `gh run rerun RUN_ID`（Re-run all jobs）；没有就请 collaborator 代跑
- 新 push 会取消旧 CI run，review 修改攒一批再推

## Review 与合并

- merge oncall（当前 @BobbyZhouZijian）自动 assign，按区域路由 reviewer
- 合并条件：≥1 maintainer 批准 + 每个实质影响区域的 reviewer 批准 + 检查全绿 + 无阻塞讨论 + 有测试 + 文档更新
- CI 全绿不保证合并（还看方向、维护成本）
- 60 天不活跃标 stale，再 21 天关闭
- **无 DCO/CLA**，贡献即同意 Apache 2.0

## Commit 风格

- 无成文规范，但事实惯例是 **Conventional Commits + squash merge**：`type(scope): 描述 (#PR号)`
- 分支内 commit 随意，建议 PR 标题直接写成 conventional commit 格式

## 红线

- 绝不提交凭证/密钥/私密 transcript
- 不做投机修复、无关格式化
- AI 辅助必须人工逐行理解并负责

## 上游近期 merged PR 观察（2026-09，60 个样本）

**内容分布**：
- **harness 演化 / reefine / reef-pi 是绝对主线**（约一半）：proposer 闭环、E2B sandbox、会话恢复、slash command、版本历史 UI
- **训练方法 recipe**：SDFT、SPADE、SAO/CEO-Bench、distillation 后端、Tinker 集成
- **record2dataset / task player**：Designer、Harbor 任务、verifier reward
- **基建**：observability（OTel spans、W&B）、CI 提速/分层、OpenAI Responses 端点
- 纯 docs/依赖 bump 占少数

**风格规律**：
- 标题全是 conventional commits，且描述**面向用户价值**而非实现（"recover E2B disconnects and surface failed proposals"）
- fix 标题会说清**症状**（"make the proposer prove a change by its effect, not by the call returning"）
- 单 PR 单主题；大功能是拆成一串小 PR 连续合的（reefine 系列）
