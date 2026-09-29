# 模型侧与权重迭代链路 学习笔记

> 一句话：reef **不实现任何训练/推理内核**。权重存在于两个外部系统的进程里——推理侧 SGLang、训练侧 Slime/Megatron（Ray actors）。reef 是编排者。

## 1. 模型权重在哪

| 侧 | 位置 | 说明 |
|---|---|---|
| 推理 | `reef/inference/sglang/` | SGLang 引擎进程，Reef 通过 HTTP 调用控制 |
| 训练 | `reef/train/slime_backend/` | Slime 的 Megatron Ray actor 组，**真正的梯度更新在这里** |

## 2. 推理侧关键类（reef/inference/sglang/）

- `engine.py` `SGLangEngine`：HTTP 客户端。三条权重更新通道：
  - `update_weights_from_tensor`（:212）NCCL 张量直传
  - `update_weights_from_disk`（:432）磁盘 checkpoint
  - LoRA：`load_adapter_files`
- `backend.py` `SGLangInferenceBackend`（:15）：pause/resume 准入、colocate 显存让渡（offload/onload）

## 3. 训练侧关键类（reef/train/slime_backend/）

- `reef_adapters/bridge.py:81` `SlimeTrainingBackend`：`train_job`（:253）调 Slime 的 Megatron actor 组做梯度更新；`save_job_checkpoint`（:277）落盘
- 算法在 `algorithm.py` / `loss_families.py`；recipe 方法在 Megatron worker 内解析
- 其他后端：`tinker_backend/`（Tinker API）、`cordis_backend/`（harness 演化，不动权重）

## 4. 权重发布链路（两阶段事务！）

```
Trainer.execute_reserved_step
 → RuntimeCandidateBackend.settle_step → activate_candidate
 → RuntimeScheduler.activate_candidate (scheduler.py:629)
     inference_runtime.pause_admission()   ← 推理准入关闭
 → "update_serving_weights" RPC → TrainingPublication.publish (publication.py:721)
     marker: CHECKPOINT → UPDATING_WEIGHTS（durable）
     传输三选一：NCCL 张量 / HF 磁盘导出 / LoRA 文件
     _verify_engines_serve 校验每个引擎版本
     marker → READY_TO_COMMIT（引擎仍暂停！）
 → ScenarioCommitter.commit (committer.py:283)   ← Reef durable commit 屏障
 → acknowledge → marker COMPLETE → 恢复推理准入
```

**关键不变量：权重先到引擎、Reef commit 后才对流量可见。** 崩溃恢复靠 marker 文件（FileTrainingJobStore）+ commit log 决定重放或回滚。

## 5. 版本与并发

- `RuntimeLoadId` = incarnation + sequence，单调递增；每条 rollout 样本盖版本章
- 训练侧做 staleness 准入（scheduler.py:215）——太旧的样本不训
- `lora_mode: "scenario"` 时多场景共享一个训练运行时，每场景一个 LoRA adapter，`AdapterResidencyManager`（publication.py:139）管引擎里的驻留槽位

## 6. 模型生命周期总图

```
HF 初始权重 → SGLang 引擎加载 → rollout（样本带版本号）
→ Dispatcher 攒 batch（staleness 检查）
→ Slime/Megatron 梯度更新 → checkpoint
→ 权重传输（NCCL/磁盘/LoRA，全程暂停准入）
→ Reef commit → 准入恢复 → 引擎以新版本服务
```
