# 权重版本控制 学习笔记

> 核心：`RuntimeLoadId` = `incarnation:sequence`。每条 rollout 样本盖章，训练前做 staleness 准入。

## 1. RuntimeLoadId（reef/runtime/interfaces.py:52）

```python
@dataclass(frozen=True)
class RuntimeLoadId:
    incarnation: str   # 服务化身 = uuid4，重启即变
    sequence: int      # 化身内单调递增，每次权重发布 +1
```

- **为什么需要 incarnation**：重启后序号归零，旧 token 不能复用——"Backends must namespace monotonic counters by a fresh serving incarnation so a restart cannot reuse an old token for different weights"（interfaces.py:524-532）
- **没有实现 `<` 比较**：先比 incarnation（不同 = `cross_incarnation`，不可比），同化身内比 sequence 差值（lag）

## 2. 版本号流动：盖章 → 回流 → 准入

**盖章**（推理侧 → record）：
- `reef/surface/weights.py:234` 从 SGLang 响应的 `meta_info.runtime_load_id` 提取版本
- 支持**逐 token span**（生成中途换权重的场景），校验 span 单调性
- 落章处 `request_service.py:467` `_stamp_durable_runtime_load_id`：只对训练场景盖章；训练响应没版本号直接抛 `RuntimeLoadMismatch`

**准入**（训练侧执行前）：
- 组 batch 时每条样本附 `producing_runtime_load_ids`（train/runtime.py:83-121）
- `scheduler.py:215` `_staleness_admission` 判决：
  - `lag > max_staleness` → drop（`policy_lag_exceeded`）
  - incarnation 不同 → drop（`cross_incarnation`，旧引擎数据永远进不了新化身）
  - 版本比当前还新 → drop（`future_producing_runtime_load_id`）
- 阈值：`max_staleness`，env `REEF_MAX_STALENESS`，**默认 0 = 完全同步 RL**（recipe 层和运行时层双配置，显式校验一致）
- 被 drop 的批次 → `StaleCandidate` → `reject_pending`，记录 `staleness/*` 指标

## 3. 版本递增的纪律

- **Reef 是版本的权威分配者**：`BackendWeightPublisher.next_runtime_load_id()`（publication.py:1062）指派目标版本，sender 实际导出的版本必须等于目标，不等直接 RuntimeError（train_groups.py:250-252）
- **只有 commit 完成的发布才推进服务版本**：失败的 swap 可能消耗后端计数器，但 `serving_runtime_load_id` "caches only completed publications"（scheduler.py:891-898）
- 新启动的 sender 被强制绑到 Reef 恢复出的版本（`initialize_exact_runtime_load_id`），不允许自创序号

## 4. 崩溃恢复的对齐

- 探针发现引擎版本不一致时**不换版本号重发**（`republish_serving`，scheduler.py:900-908）："Keep the current token because the checkpoint/model tensors have not changed"
- `recover_pending_step`（scheduler.py:673）对照 durable marker（UPDATING_WEIGHTS → 重放；COMPLETE 未 ack → 补 ack）与 commit log 和解

## 5. per-scenario LoRA 模式的版本控制

- 引擎的 runtime_load_id 是**引擎全局**的（任何场景发布都推进它），序号差会**高估**单场景滞后
- 改用 `_scenario_staleness_admission`（scheduler.py:271-319）：lag = 该样本之后**这个场景**发布了几次，查 `ScenarioHistoryStore.lag(scenario, version)`
- adapter 名编码场景+版本；`AdapterResidencyManager`（publication.py:139）以 `(scenario, runtime_load_id)` 为键管理引擎内驻留槽位，支持容量驱逐（有 pin 保护）、引擎重建后 reconcile

## 6. 设计启示

1. **版本号 = 命名空间 + 序号**，重启换命名空间，杜绝"序号回绕撞车"
2. **不提供偏序比较**，强制调用方显式处理"跨化身不可比"的情况
3. **版本分配权集中**在 Reef，sender 只执行，杜绝多方发号
4. **全局序号在共享资源下会失真**（LoRA 模式），需要按维度（场景）重新建模滞后
5. staleness 是**显式策略配置**而非隐藏行为，默认最严（0）

## 7. 版本派生（"生蛋"）调查结论

**不支持从旧版本分叉出新版本线**——每个 scenario 的版本链严格线性：

- `ArtifactReleaseChain`（reef/artifact/release_chain.py:17）：一条有序链，`publish` 的 `expected_parent` 缺省 = 当前 head，不匹配直接 `ArtifactConflict`（乐观锁，git_lfs.py:382）
- 用户可见的 `vN` 只是按提交序号渲染的标签，不是 DAG 节点
- 演化逻辑里没有"指定从某旧版本出发提案"的参数

**最接近"生蛋"的两个机制**：

1. **rollback/promote**（committer.py:153）：把旧版本**内容复制**为新的线性 head——"Publish a durable copy of an older version as a new fenced commit"。v{N+1} 内容 == 旧 vK，但 parent 仍是 vN，历史永远向前。想"从 vK 继续演化"只能先 rollback，代价是链上多一个节点且 vK 之后版本退出主链。
2. **scenario 级 fork**（git_lfs.py:300）：创建新 scenario 时从 base artifact 分叉出独立仓库，新 scenario 走自己的线性链。是"新谱系的起点选择"，不是同谱系内的分支。

**如果要做真正的版本分支**：改持久化格式和版本契约，按 CONTRIBUTING.md 标准需要 RFC。
