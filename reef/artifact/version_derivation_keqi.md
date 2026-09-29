# 版本派生（"生蛋"）机制调研

> 问题：reef 能否从一个旧版本衍生出新版本？
> 结论：**没有分支式派生，只有线性链上的"复制式回生"和 scenario 粒度的 fork。**

## 1. 版本模型：严格线性链

- `ArtifactReleaseChain`（reef/artifact/release_chain.py:17）："one ordered chain of immutable releases"
- `publish` 的 `expected_parent` 缺省 = 当前 checkpoint head（:78）；不匹配 → `ArtifactConflict`（git_lfs.py:382）——乐观锁，不是分支点
- 用户可见的 `vN` 标签只是按提交序号渲染（release_page.py:568），背后不是 DAG
- step 计数严格线性：`advance_to` 强制 `step == self._step + 1`（committer.py:143-146）
- 演化逻辑中没有"指定从某旧版本出发提案"的参数；候选评估不通过即丢弃，链上不留痕

## 2. 两个"类生蛋"机制

### rollback / promote —— 复制旧内容为新后代

`ScenarioCommitter.rollback`（reef/scenario/committer.py:153）：

> "Publish a durable copy of an older version as a new fenced commit; promote uses the same path."

流程：
1. `find_release` 找旧版本（:162）；要求有 durable checkpoint 字节，live weights 不可恢复（`ReleaseNotRestorable`）
2. 拒绝在训练 pending 时执行（:170-171）
3. 旧内容 stage 为 `next_step = self._step + 1` 的新 artifact，**parent 仍是当前 checkpoint**（:196）
4. 正常 publish + commit record，metadata 记 `rollback.target_release_id`（:213-215）
5. 训练 checkpoint 还会 `restore_checkpoint` 回灌训练侧（:188-191）

**效果**：v{N+1} 的内容 == 旧 vK，但谱系上 parent 是 vN。历史永远向前追加。
想"从 vK 继续演化"→ 先 rollback 到 vK，代价：链上多一个 rollback 节点，vK 之后的版本退出主链。

### scenario 级 fork —— 新谱系的起点

`RepositoryBackend.fork(release_id)`（repository.py:31；git_lfs.py:300；memory.py:97）：

- 调用点：`reef/scenario/factory.py:103` —— **创建新 scenario** 时从共享仓库分叉
- `Repository.fork`（repository.py:274-280）固定从 `base_artifact` 分叉
- git_lfs 实现技术上接受任意 release_id，记录 `source={"kind": "fork", ...}`（:316）；幂等（已存在直接返回）
- fork 后新 scenario 走自己的线性链

## 3. 需求对照表

| 需求 | 支持？ | 机制 |
|---|---|---|
| 回到旧版本服务 | ✅ | rollback/promote：复制旧内容为新 head |
| 从旧版本内容继续演化 | △ 间接 | 先 rollback，后续从新 head 继续 |
| 同一 scenario 内并行版本线 | ❌ | expected_parent 乐观锁 + 单 head |
| 从某版本分叉出新 scenario | ✅（创建时） | `RepositoryBackend.fork` |

## 4. 为什么这样设计

线性链 + 乐观锁让"哪个版本在服务"永远有唯一答案。配合权重发布的两阶段事务（权重先到位、Reef commit 后才可见），推理侧、训练侧、持久层对"当前版本"的认知不会分叉。DAG 会让这套一致性协议复杂得多。

## 5. 若要真正的版本分支

属于架构级变更（改持久化格式和版本契约），按 CONTRIBUTING.md 需先开 RFC issue。上游当前主线正是 harness 演化，"从表现好的旧 harness 并行探索多个方向"是一个真实痛点，此类 RFC 可能得到认真讨论。
