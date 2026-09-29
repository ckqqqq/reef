# Executor RPC 机制学习笔记

> reef 的进程编排通信层：orchestrator 通过它控制所有服务的生命周期。

## 1. 是什么

**不是 HTTP/socket RPC**，而是一套"按 worker rank 调用方法"的抽象：

- 抽象基类 `Executor`（ABC）：`reef/runtime/executor/base.py:132`
- 核心方法：`rpc(rank, method, args...)`（:233）对单个 rank 按**字符串方法名**派发；`collective_rpc`（:221）对所有 rank 广播
- `rpc(0, ...)` 的 `0` = worker rank 编号；deploy 场景每个服务只有一个 rank-0 worker
- `status/prepare/start/probe/...` 不是 Executor 接口方法，而是 **worker 对象上的普通方法名**，由 rpc 派发

## 2. 三种传输实现

| 后端 | 类 | 传输 |
|---|---|---|
| `uni` | `UniProcExecutor`（uniproc.py:61） | 同进程线程池里直接 `getattr(worker, method)()` |
| `mp` | `MultiprocExecutor`（multiproc.py:164） | `multiprocessing` spawn + **Pipe + pickle** |
| `ray` | `RayExecutor`（ray.py:64） | **Ray actor method call**（`.remote()` → ObjectRef） |

- mp 消息格式：请求 `pickle.dumps((method, args, kwargs))`，响应 `pickle.dumps((ok, value))`
- mp 子进程侧循环 `_serve`（multiproc.py:50）：收 → 调用 → 回传
- ray 强制 `max_task_retries=0`，遵守"绝不自动重试 mutating RPC"契约

## 3. 中间层 `SubmittingExecutor`（base.py:259）

公共的 rpc/collective_rpc 实现一次，子类只实现 `_submit(rank, ...)` 和 `_submit_all`，返回 `ExecutorFuture`。**超时只限制等待，不取消 worker 侧工作**。

## 4. RPC 的业务实现：`ProcessWorker`（reef/service/deploy/process.py:47）

orchestrator 调的 9 个方法都在这：

- `prepare`（:257）：写 `run_dir/runtime.yaml`（0600 权限）
- `start`（:138）：`subprocess.Popen`，日志重定向到 `{name}.log`，`start_new_session=True` 建独立进程组，写 pid 文件
- `probe`（:269）：跑服务的 ready 检查命令
- `status` / `read_log`（增量 offset tail）/ `describe`
- `request_stop`（:301）/ `tree_alive` / `shutdown`（:202，SIGTERM → grace → SIGKILL → 5s reap）

**进程组管理是精华**：`os.killpg` 整组发信号（覆盖 Ray/SGLang 这类 leader 死后留 worker 的启动器），`_safe_process_group` 防 PID 复用误杀。

`RayProcessWorker`（:321）：跑在 Ray actor 里，`host` 返回节点 IP —— 这就是 endpoint `{host}` 占位符在远程节点的解析来源。

## 5. 完整调用链（start 示例）

```
_Stack._start_service (orchestrator.py:216)
 → executor.rpc(0, "start", timeout=30)
 → SubmittingExecutor.rpc → _submit
 → 传输三选一：uni 线程调用 / mp pickle+Pipe / ray actor.remote()
 → ProcessWorker.start (process.py:138) → Popen(service 命令, REEF_CONFIG=...)
 → python -m reef.service → run_service() (orchestrator.py:665) → build_app() → aiohttp
```

**四层进程模型**：orchestrator CLI → Executor worker（supervisor）→ service 进程 → 引擎子进程

## 6. 设计要点

- **失败即整组**：任一 rank 死亡通过 FailureState 广播（mp 有 sentinel 监控线程，ray 有心跳）
- **borrowed vs owned**：attach 来的 worker，shutdown 绝不杀
- **统一超时语义**：超时报错明确告知"worker 侧可能还在跑"
- **spawn 而非 fork**：避免继承 CUDA 状态
- **父死子随**：mp 子进程监听父进程 sentinel，父死即自杀

## 7. 抽象设计的可借鉴之处

1. **传输可替换，语义不动**：uni/mp/ray 三种后端共享同一套语义契约（保 rank 序、超时语义、失败传播），调用方零感知。关键做法是把公共逻辑（超时包装、open 检查）收敛到 `SubmittingExecutor` 中间层，子类只实现 `_submit` 一个原语——**"接口窄化"**：抽象层越薄，实现越不容易跑偏。

2. **契约用文字钉死，不靠自觉**："绝不自动重试 mutating RPC""borrowed worker 绝不 kill""超时不取消 worker 侧工作"——这些都写在 docstring 里且每个后端一致遵守。抽象的价值一半在接口签名，另一半在这些**不变量**。

3. **失败语义优先于成功路径**：任一 rank 死 = 整组失败（FailureState 广播）；超时错误信息明确说"worker 可能还在跑"。分布式代码的 bug 大多出在失败路径，先定义失败语义再写实现。

4. **资源生命周期分层**：四层进程模型里每层只管自己那层——orchestrator 管拓扑和顺序，Executor 管传输，ProcessWorker 管进程组，service 管业务。每层都有明确的 cleanup 责任（逆序拆除、幂等关闭、父死子随），没有跨层伸手。

5. **防御真实的坑，不防御想象的坑**：`_safe_process_group` 防 PID 复用、`killpg` 防孤儿 worker、spawn 防 CUDA 状态继承——每个防御都对应一个真实故障场景，没有投机性的 fallback。

6. **不变量单一化**："全系统唯一提交点"（ScenarioCommitter）、"一个进程最多一个权重训练场景"——把并发正确性压缩成少数几条可陈述的不变量，比到处加锁可靠。

## 8. 也有可警惕之处

1. **字符串方法名派发**（`rpc(0, "start")`）：绕过了类型检查，调用方写错方法名运行时才炸。reef 自己的 AGENTS.md 都禁止 `getattr` 探测能力，这里却是核心机制——属于"边界适配器内的必要动态性"，但如果你写类似框架，可以考虑给 worker 方法定义显式接口 + 注册表。

2. **一个文件两个角色**（orchestrator.py 里父进程编排 + 子进程入口 run_service）：靠延迟 import 和注释维持边界，新人容易读混。分开文件可能更清晰。

3. **四层进程**的调试成本：日志要经过 read_log RPC 增量回传才能看到，排查问题时链条长。抽象换来的部署灵活性是有运维代价的。
