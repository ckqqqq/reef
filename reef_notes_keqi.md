# Reef 源码学习笔记索引

> 用 AI 辅助阅读 reef 源码的系列笔记。按理解链路排序，建议按序阅读。
> 所有笔记均为个人学习记录，已被 `.gitignore` 的 `*keqi.md` 规则忽略。

## 理解链路（自外向内）

### 1. 进程编排层 —— reef 怎么启动

[reef/service/deploy/orchestrator_keqi.md](reef/service/deploy/orchestrator_keqi.md)

`reef serve` 背后的进程编排器：`_Stack` 依赖拓扑启动、endpoint 先发布后启动、两阶段信号处理、逆序优雅关闭。一个文件两个角色（父进程编排 + 子进程入口 `run_service`）。

### 2. 进程控制协议 —— 编排器怎么控制子进程

[reef/runtime/executor_keqi.md](reef/runtime/executor_keqi.md)

Executor RPC：按 worker rank 派发字符串方法名的抽象，uni/mp/ray 三种可插拔传输（直接调用 / pickle+Pipe / Ray actor）。含**抽象设计启示**：接口窄化、契约不变量、失败语义优先。

### 3. 模型侧 —— 权重在哪、怎么迭代

[reef/train/model_iteration_keqi.md](reef/train/model_iteration_keqi.md)

reef 不实现训练/推理内核：推理在 SGLang、训练在 Slime/Megatron（Ray actors）。权重发布是**两阶段事务**：权重先到引擎 → Reef durable commit → 才对流量可见。

### 4. 版本控制 —— RuntimeLoadId

[reef/train/version_control_keqi.md](reef/train/version_control_keqi.md)

`incarnation:sequence` 结构（重启换化身防序号撞车）、样本盖章 → staleness 准入（`max_staleness` 默认 0 = 同步 RL）、Reef 是版本唯一权威分配者、LoRA 多场景下全局序号失真的解法。

### 5. 版本派生 —— "生蛋"调研

[reef/artifact/version_derivation_keqi.md](reef/artifact/version_derivation_keqi.md)

结论：版本链严格线性，不支持分支式派生。rollback/promote = 复制旧内容为新线性 head；scenario 级 fork = 新谱系起点。若需真正分支属于 RFC 级变更。

## 工程协作

### 6. PR 提交指南

[pr_guide_keqi.md](pr_guide_keqi.md)

RFC 定级标准、PR 模板 6 节、CI 分层（Draft 轻量 / ready 3.12 / 合并前全矩阵）、上游近期 PR 风格观察（harness 演化是主线、标题面向用户价值）。

## 一张总图

```
reef serve (orchestrator)  ──Executor RPC──▶  ProcessWorker ──▶  reef.service (aiohttp)
                                                                │
                                          HTTP 请求 → Dispatcher.accept_record 落盘
                                                                │ 唤醒训练线程
                                          Trainer 攒 batch（staleness 准入，版本章）
                                                                │
                                          Slime/Megatron 梯度更新 → checkpoint
                                                                │
                                          权重传输（NCCL/磁盘/LoRA，暂停准入）
                                                                │
                                          ScenarioCommitter.commit（唯一提交点，线性链）
                                                                │
                                          恢复准入，引擎以新 RuntimeLoadId 服务
```
