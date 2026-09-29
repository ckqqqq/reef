# SAO 分层冒烟验证

目的：先区分配置错误、CUDA 计算错误和完整训练环境缺失，再启动昂贵的训练。
以下命令从 Reef 仓库根目录执行。独立工作目录不会切换正在提供服务的源码。

## 1. 环境准备

```bash
conda activate reef
uv venv --python "$CONDA_PREFIX/bin/python" .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
# DGX Spark / CUDA 13；其它机器选择对应的 PyTorch CUDA wheel。
uv pip install torch --index-url https://download.pytorch.org/whl/cu130
# 保留仓库 runtime pin，不让 Slime 覆盖 CUDA 依赖。
uv pip install --no-deps --group runtime
```

**ARM64 实测例外**：该 runtime pin 的 setup.py 第 24 行把 Linux wheel 标成
`manylinux1_x86_64`，上述最后一步在 Spark 安装失败。
不要把 x86 wheel 强行改名安装。仅做配置测试时，可以直接导入同一 pin 的源码：

```bash
mkdir -p .sao-smoke
git clone https://github.com/THUDM/slime.git .sao-smoke/slime
git -C .sao-smoke/slime checkout 41014d1f29e201137fdffce737bb8bac65bc5219
export PYTHONPATH="$PWD/.sao-smoke/slime:$PWD/tests:$PWD"
```

`.sao-smoke/` 为本地测试产物目录，不要提交。源码导入只绕开打包标签，
没有安装 Megatron/SGLang，也不证明 Slime 在 ARM64 上能完整训练。
[固定源码的打包代码](https://github.com/THUDM/slime/blob/41014d1f29e201137fdffce737bb8bac65bc5219/setup.py#L24)。

## 2. 配置与 Reef 控制流程

```bash
python -m pytest -o addopts= -q \
  tests/reef_service/test_sao_configs.py \
  tests/reef_service/test_sao_reference.py \
  tests/reef_service/test_sao_pipeline.py \
  tests/reef_service/test_sao_parity.py
```

- configs：调用 Slime 的真实参数定义，但对缺失的 SGLang 等模块使用导入替身。
  Megatron 参数只检查名称，SGLang 参数不做完整验证。详见该测试的模块说明。
- reference/parity：检查 SAO 数学定义与实际张量代码一致。
- pipeline：检查反馈、训练调度和发布流程；训练运行时是替身，不代表 GPU 训练成功。
- 必须检查 skipped 和错误，不能只看 pytest 返回码。

## 3. CUDA 上的真实参数更新

```bash
REEF_SAO_CUDA_SMOKE=1 python -m pytest -o addopts= -q -s \
  tests/smoke/test_sao_cuda.py
```

验证 FP32/BF16 两种精度下的真实 torch.compile SAO 损失、反向传播、SGD 更新、
被屏蔽 token 的零梯度，以及检查点保存/加载。预期每种精度最大参数变化为 0.03125。
显式启用后缺少 torch/CUDA 会失败；默认未启用时跳过，避免 CPU CI 意外启动 GPU 测试。

这是合成 log-probability 参数的数值冒烟，不是语言模型训练，不包含 critic、rollout、
Megatron 分布式初始化、SGLang 权重同步或 Reef 新版本推理。

## 4. 进入完整 SAO 训练前

当前 IMOAnswerBench 小模型配置：
`recipes/sao/examples/imo_answerbench/serve.yaml`。

- 使用 Qwen2.5-1.5B-Instruct 原始权重，不是 Ollama 的 GGUF 量化文件。
- 默认训练/推理分开使用两张 GPU。Spark 只有一张 GB10，不能原样启动。
- Reef 支持 colocate 分支，且要求训练与推理 offload；这不等于单卡 SAO 已实测通过。
- `batch-size` 和 `global_batch_size` 必须一致。
- 默认 `num-critic-only-steps: 10`；前十步只更新 critic。
  检查 actor 权重变化必须越过 warmup，或在明确标注的实验配置中缩短 warmup。
- 完整验收应保留：训练日志、actor 权重前后差异、发布的 release ID、
  推理端实际加载的版本，以及新版本真实回答。仅 release 增加不足以证明 actor 更新。

完整运行时导入检查（缺少任一项就先补环境）：

```bash
python -c 'import torch, ray, slime, sglang, megatron.core, transformer_engine; print(torch.cuda.get_device_name(0))'
```

## 2026-09-29 DGX 实测

基线：learning-notes / 96f938f。硬件：单张 NVIDIA GB10，aarch64。
Python 3.12.14；PyTorch 2.14.0+cu130；Triton 3.8.0；CUDA toolkit 13.0。

| 验证 | 结果 | 证明的范围 |
| --- | --- | --- |
| SAO reference + pipeline | 56 passed | 数学定义、Reef 流程（训练替身） |
| SAO configs + parity | 56 passed | 固定 Slime 源码的配置参数、CPU 张量数学 |
| 新增 CUDA 冒烟 | 2 passed | FP32/BF16 真正反传和 SGD 参数更新，最大变化均 0.03125 |
| pre-commit --all-files | passed | 全仓库已有静态检查 |
| mypy | 336 files，passed | 静态类型 |
| 全 tests/ | 收集阶段 12 errors、3 skipped | 未通过；不能宣称全套测试成功 |

相关测试共 114 项通过。完整 SAO 模型训练、critic 更新、权重发布、
新版本推理均未执行。Ray、SGLang、Megatron、Transformer Engine 尚未安装。
全套测试的收集错误为缺少 ray，以及当前 PyPI reef-client 不含 reef_client.record_import；
完整开发测试还需要仓库固定的 reef-client 子模块。
Slime 的 wheel 架构标签失败已经复现；未修改 Slime 源码来掩盖此问题。

测试日志保存在 DGX 工作目录 `~/Fun/reef-learning/.sao-smoke/`：
`reef-tests.log`、`config-tests.log`、`cuda.log`、
`precommit-all.log`、`mypy.log`、`full-suite.log`。
