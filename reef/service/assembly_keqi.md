# reef/service/assembly.py 装配层学习笔记

> 273 行。一句话：**组合根（Composition Root）**——ServiceConfig 进，aiohttp app 出。

## 1. 职责边界

docstring（:1-7）："It knows nothing about the deployment config format"

三层分离：
```
deploy/      认识 YAML/CLI/进程编排
assembly.py  只认识 ServiceConfig（本层）
app/dispatcher 只认识组件（运行时行为）
```

## 2. build_app（:251）流程

1. 横切配置值对象化：RecordRetention、InferenceRetryPolicy
2. `build_dispatcher` → Dispatcher（唯一重依赖）
3. `create_app`（app.py:36）注入：dispatcher + tokens + CORS + retry + `close_dispatcher=True`（生命周期级联）
4. 失败补偿：create_app 失败则 dispatcher.close()

## 3. build_dispatcher（:193）装配顺序

| 组件 | 配置来源 |
|---|---|
| Recipe | `recipe` 字段，三种加载：WeightTrainingRecipe 子类 / dotted 类 / 内置名 preset |
| Record 存储 | `record_backend`：postgres → PostgresScenarioStorage，否则 SQLite |
| 追踪装饰器 | `tracing_config` → ObservedScenarioStorage 包装 |
| Artifact 仓库 | GitLFS backend + `bootstrap_files=recipe.base_artifact_files()` |
| 实验追踪 | `wandb_config` → W&B |
| Dispatcher | 以上全部 |

**失败补偿贯穿**：任一环节失败，已建组件逆序 `suppress(Exception)` 关闭后重抛。

## 4. 训练运行时连接（_connect_training_runtime :70）

三层可插拔，assembly 只走插件协议，**没有任何 `if backend == "slime"` 硬编码**：
- `training_deployment_for`：builtins → **entry point 组**（第三方 pip 包可注册）→ dotted 路径
- backend 把 ServiceConfig 翻译成 runtime_config（拓扑细节封装在这步）
- `RuntimeRegistry` 按 kind 构建运行时，必须返回 (TrainingRuntime, InferenceRuntime) 对

## 5. ServiceConfig（deploy/service_config.py:25）

约 40 个字段的 dataclass，每个 `config_option` 声明默认值 + YAML 路径 + help。分组：服务（host/port/tokens）、训练（backend/ray/colocate）、推理上游（upstream_*）、存储产物、横切（wandb/tracing）。

## 6. 设计启示

1. **组合根模式**：装配集中一处，业务组件不认识配置文件
2. **手工 DI，无框架**：注入点就是函数参数（`environ`、`connector` 专为测试留口）
3. **配置键所有权**：`_recipe_owned_settings`（:45-57）把 recipe 的键和 service 的键分开，未声明的键**报错**而非静默忽略——防止运维写错键名后服务跑在默认值上
4. **资源所有权随返回值转移**，异常安全靠逆序清理
5. **延迟 import**（orchestrator.py:671）保证轻量 CLI 路径不拉起训练栈
