# reef/service/deploy/orchestrator.py 学习笔记

> 677 行。一句话：**`reef serve` 背后的进程编排器**。

## 1. 整体职责

- docstring（:1-7）："Process orchestrator behind `reef serve`"
- 并发启动多个服务、处理 readiness 依赖、镜像子进程日志、看门狗监控、信号到来时**逆序**拆除
- 职责边界：HTTP 应用装配在 `assembly.py`，CLI 语法在 `cli.py`，本文件只做进程编排

## 2. 关键架构：一个文件，两个相反角色

```
父进程: main() (:624) → _run_orchestrator() (:473) → _Stack 编排子进程
                                                        │ Executor RPC
子进程: reef/service/__main__.py → run_service() (:665) → build_app() → aiohttp
```

- orchestrator **不直接运行** HTTP 服务，通过 Executor RPC 拉起子进程
- `run_service()` 是子进程入口，靠环境变量 `REEF_CONFIG` 拿配置路径

## 3. 核心类 `_Stack`（:93）

跨 Executor RPC 的依赖编排，与放置（本地进程 / Ray）无关：

| 方法 | 行号 | 职责 |
|---|---|---|
| `_prepare_services` | :160 | 批量 prepare；必要时自建 Ray 集群；**先发布所有 endpoint 再启动任何进程** |
| `_start_service` | :216 | prepare RPC → start RPC → 写 worker.json → `_wait_ready` |
| `start` | :233 | 主循环：ThreadPoolExecutor 并发启动，按 depends_on 拓扑解锁 |
| `_watchdog` | :273 | 稳态看门狗，每 5s 探活+收日志，意外退出则停全栈 |
| `block` | :291 | 稳态阻塞，信号处理器只置标志位；第二次 Ctrl-C 跳过优雅期 |
| `shutdown` | :311 | 逆序关闭：request_stop → 30s grace → shutdown(grace=0) 强清 |

## 4. `run_service()`（:665）子进程流程

1. 取配置路径（参数或 `REEF_CONFIG` env）
2. `load_config` → `service_config_from_mapping` → `ServiceConfig`
3. **延迟 import** `assembly.build_app`（避免 CLI 场景拉起训练栈）
4. `web.run_app(app, host, port)` —— socket 绑定就这一行，无 TLS（认证在 app 层）

## 5. `_run_orchestrator()`（:473）父进程流程

load YAML → `resolve_deployment_config`（纯解析不起进程）→ `startup_report` 诊断 → `validate_services` → 写 override 临时配置 → 建 run_dir → `_Stack` → 装启动期信号处理器 → `start()` → `block()` → finally: `shutdown()` + 清理临时配置

## 6. 与 assembly.py 的衔接（单向）

```
run_service → build_app(settings) (assembly.py:251)
    → build_dispatcher(settings) (assembly.py:193)  # recipe/artifact/record store → Dispatcher
    → create_app(dispatcher, ..., close_dispatcher=True) (app.py:36)
```

orchestrator 不 import build_app（函数内延迟导入），编排层与装配层解耦。

## 7. deploy/ 兄弟模块

`cli.py`（参数解析）、`config_utils.py`（加载/环境插值）、`deployment_config.py`（schema v2 layout 翻译）、`diagnostics.py`（启动报告）、`execution.py`（executor 选择）、`inference.py` / `training.py` / `generator.py`（各类服务装配）、`service_config.py`、`process.py`（子进程 supervisor）

## 8. 值得注意的设计细节

- **两阶段信号处理**：启动期信号抛 KeyboardInterrupt；稳态后处理器只置标志位不做 I/O（避免打断持锁的 Event.wait）。第二次 Ctrl-C 强制关闭（commit 5ffac29 修的就是这里）
- **endpoint 先发布后启动**（:182-184），`{host}` 占位符 prepare 阶段解析
- **Executor RPC 化**：status/read_log/probe/prepare/start/request_stop/shutdown 全走 RPC，放置后端可插拔
- **配置不可变纪律**：启动任务不改 deployment config，每个服务拿 deepcopy（:249）
- **关键超时**：grace 30s、watchdog 5s、ready_timeout 默认 3600s（模型下载慢）、probe 5s
- **错误分层**：DeployConfigError（配置）→ DeployStartupError（启动，附日志目录）→ 稳态退出码
- **幂等关闭**：`_closed` 标志防重入，单步失败不中断整体拆除
