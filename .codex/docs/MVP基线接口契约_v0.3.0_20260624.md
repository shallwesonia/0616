# MVP 基线接口契约 v0.3.0-mvp-baseline

## 基线信息

| 项 | 内容 |
|---|---|
| 基线版本 | `v0.3.0-mvp-baseline` |
| 形成日期 | 2026-06-24 |
| 适用范围 | 当前单 Workspace MVP、多机器人运行态、Target Registry、机器人配置、执行体管理、仿真驾驶舱 |
| OpenAPI | `docs/contracts/openapi.json` |
| AsyncAPI | `docs/contracts/asyncapi.yaml` |

## 冻结范围

本基线冻结以下能力，后续变更应通过兼容新增或版本升级处理：

- REST API 路径、请求体、响应体和错误码。
- MQTT Topic 命名、Command payload、Result/Event/Telemetry payload。
- 数据库核心表结构和 Workspace 隔离字段。
- 前端页面入口：`/`、`/simulation`。
- 仿真驾驶舱核心流程：选场景、建任务、发指令、看状态、看消息、注入异常。

## REST API 基线

核心 API 分类：

| 分类 | 代表接口 |
|---|---|
| 健康与连接 | `GET /api/v1/health`、`GET /api/v1/connections` |
| 地图 | `GET /api/v1/maps/current`、`POST /api/v1/maps/{map_id}/drafts` |
| Target Registry | `GET /api/v1/targets`、`POST /api/v1/targets`、`PATCH /api/v1/targets/{target_id}` |
| 机器人运行态 | `GET /api/v1/robots`、`POST /api/v1/robots`、`POST /api/v1/robots/{robot_id}/state` |
| 机器人配置 | `GET /api/v1/robot-configs`、`POST /api/v1/robot-configs`、`PATCH /api/v1/robot-configs/{robot_code}` |
| 执行体 | `GET /api/v1/executors`、`POST /api/v1/executors`、`POST /api/v1/executors/{executor_id}/stop` |
| 消息总成 | `GET /api/v1/messages`、`POST /api/v1/messages`、`POST /api/v1/commands` |
| 仿真运行 | `POST /api/v1/simulation-runs`、`POST /api/v1/simulation-runs/{run_id}/start` |
| Task/Plan/Action | `POST /api/v1/simulation-runs/{run_id}/tasks`、`POST /api/v1/simulation-runs/{run_id}/task-chains`、`POST /api/v1/tasks/{task_id}/plans`、`POST /api/v1/actions` |
| 规则调度 / AgentDecision | `POST /api/v1/simulation-runs/{run_id}/schedule`，支持把当前 Task 的下一个 Pending PlanStep 推进为 Action |
| Trace/State | `GET /api/v1/current-states/{run_id}`、`GET /api/v1/traces/{trace_id}/graph` |

完整字段以 `docs/contracts/openapi.json` 为准。

### Scene / World State Hub 兼容 API

为对齐原文 Hub “当前外部可调用接口与实现状态”，平台新增一层兼容 API。兼容层不替换现有平台 API，而是在 `/api/v1` 下提供 Hub 风格对象、字段和查询路径，便于外部系统按 Hub 原文契约接入。

兼容入口：

| 分类 | Hub 对齐接口 |
|---|---|
| Health | `GET /health` |
| Run | `POST /api/v1/runs`、`GET /api/v1/runs/{run_id}` |
| Scene | `POST /api/v1/scenes`、`GET /api/v1/scenes`、`GET /api/v1/scenes/{scene_id}` |
| Entity | `POST /api/v1/entities`、`GET /api/v1/entities`、`GET /api/v1/entities/{entity_id}` |
| Observation / CurrentState | `POST /api/v1/observations`、`GET /api/v1/current-state` |
| ExecutorResult | `POST /api/v1/executor-results`、`GET /api/v1/executor-results` |
| Task | `POST /api/v1/tasks`、`GET /api/v1/tasks/{task_id}` |
| Plan | `POST /api/v1/plans`、`GET /api/v1/plans/{plan_id}`、`POST /api/v1/plans/{plan_id}/status` |
| Action | `POST /api/v1/actions`、`GET /api/v1/actions/{action_id}`、`POST /api/v1/actions/{action_id}/status` |
| Trace | `POST /api/v1/traces`、`GET /api/v1/traces/{trace_id}`、`POST /api/v1/traces/{trace_id}/events`、`GET /api/v1/traces/{trace_id}/events` |
| Snapshot | `POST /api/v1/snapshots`、`GET /api/v1/snapshots/{snapshot_id}`、`GET /api/v1/snapshots` |
| Message Query | `POST /api/v1/messages/query` |

兼容规则：

- `POST /api/v1/actions` 同时兼容平台原 Action 请求体和 Hub Action 请求体；当请求体包含 `action_type`、`task_id`、`plan_id` 时按 Hub Action 处理。
- `action_type=move` 映射为平台动作 `goto_pose`；`parameters.target_pose=[x,y,z,yaw]` 会转换为 `x/y/z/yaw`。
- Hub Action 仍通过消息总成发布 Command，不直接操作执行体。
- Hub Action 的 `entity_id` 会解析到 `robotCode`；若传入机器人编码本身，则直接使用该机器人。
- 当前平台已有事实源的对象会映射复用：Scenario 映射 Scene，Target/Robot 映射 Entity，SimulationRun/Task/Action/CurrentState/Observation 复用原模型。
- 当前没有独立原生表的 Hub 兼容对象暂存到 MessageRecord：`scene`、`entity`、`plan`、`executor_result`、`trace`、`trace_event`、`snapshot`、`plan_status`、`action_status`。消息类型统一为 `hub.<object_type>`，来源为 `hub-compat`。
- 兼容层为外部调用契约，不改变 MQTT 机器人控制契约；设备侧仍只依赖 `factory/dogs/{robotCode}/command` 和 `factory/dogs/{robotCode}/result`。

## MQTT / AsyncAPI 基线

### Scene / World State Hub 主动同步 API（2026-06-30）

本阶段新增 0616 主动对接外部 `scene-world-state-hub` 的集成层。该层与上方“Hub 兼容 API”不同：

- Hub 兼容 API：外部系统调用 0616，0616 暴露 Hub 风格接口。
- Hub 主动同步 API：0616 调用外部 Hub，将本地 Scene / Entity / Run / Task / Plan / Action / Trace 同步到 Hub。

新增接口：

| 分类 | 接口 |
|---|---|
| Hub 状态 | `GET /api/v1/integrations/hub/status` |
| Hub MQTT 订阅说明 | `GET /api/v1/integrations/hub/mqtt-subscription` |
| ID 映射查询 | `GET /api/v1/integrations/hub/mappings` |
| Hub CurrentState 读取 | `GET /api/v1/integrations/hub/current-state` |
| Scene 同步 | `POST /api/v1/integrations/hub/sync/scenes/{scenario_id}` |
| Entity 同步 | `POST /api/v1/integrations/hub/sync/entities/{scenario_id}` |
| Run 全链路同步 | `POST /api/v1/integrations/hub/sync/runs/{run_id}` |
| Task 同步 | `POST /api/v1/integrations/hub/sync/tasks/{task_id}` |
| Plan 同步 | `POST /api/v1/integrations/hub/sync/plans/{plan_id}` |
| Action 同步 | `POST /api/v1/integrations/hub/sync/actions/{action_id}` |

同步规则：

- 0616 本地 ID 不改名，不强制转换为 UUID。
- Hub 内部对象 ID 以 Hub 返回的 UUID 为准。
- 本地对象与 Hub UUID 的关系写入 `integration.hub_id_mappings`。
- 0616 `traceId` 作为 `externalTraceId` 保存，Hub Trace 以 Hub UUID 为主。
- `where` 是查询指令，Hub 不允许作为 Action 创建；同步时只写 Trace event，并在映射中标记 `skipped`。
- Hub 不作为第二个指令源。0616 仍通过消息总成下发 MQTT command，Hub 只做世界状态、执行事实和 trace 数据承载。
- Entity 同步只推送机器人与动态/交互目标：`robot`、`cargo`、`container`、`inspectionPoint`。
- `station`、`zone`、`pathNode`、`pathEdge`、`pathGroup`、`mapObject` 以及 `metadata.source=map` 的纯地图几何不注册为 Hub Entity，避免地图对象污染运行态 Entity。
- `GET /api/v1/integrations/hub/current-state` 从外部 Hub 读取当前 Scene 的 CurrentState，并转换为 0616 驾驶舱可用的 `CurrentState` 响应；找不到 Hub Scene、Entity 或 CurrentState 时返回错误，不在读取路径自动创建 Hub 对象。

Docker Compose 默认参数：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HUB_SYNC_ENABLED` | `true` | 是否启用 0616 主动同步 Hub |
| `HUB_BASE_URL` | `http://host.docker.internal:8001/api/v1` | Hub API 地址 |
| `HUB_TIMEOUT_SECONDS` | `5` | Hub REST 调用超时 |

Topic：

| 通道 | Topic |
|---|---|
| Command | `factory/dogs/{robotCode}/command` |
| Result | `factory/dogs/{robotCode}/result` |
| Telemetry | `factory/dogs/{robotCode}/telemetry` |
| Event | `factory/dogs/{robotCode}/event` |
| Alert | `factory/dogs/{robotCode}/alert` |

约束：

- 前端不直连 MQTT。
- Agent Service 不直连 MQTT。
- 平台 API 通过消息总成发布 Command。
- 执行体只订阅自己的 command，只发布自己的 result/telemetry/event/alert。
- command topic 禁止 retained。

完整 MQTT payload 以 `docs/contracts/asyncapi.yaml` 为准。

## 数据模型基线

本基线新增或固化以下数据模型：

- Target Registry：`target_registry`
- Robot Config：`robot_configs`
- Robot Instance：`robot_instances`
- Executor Instance：`executor_instances`
- Simulation Run / Task / TaskChain / Plan / Action / Observation / CurrentState / Snapshot / Trace
- AgentDecision：当前通过 `agentDecision` 消息和 Trace 引用固化，后续可升级为独立表。

### TaskChain 与 Plan vNext 扩展

本次在保持原 Task / Plan / Action 链路不变的前提下，新增连续任务和手动 Plan 版本能力：

- `POST /api/v1/simulation-runs/{run_id}/task-chains`：创建连续任务链，将连续业务拆成多个标准 `Task`。
- `GET /api/v1/simulation-runs/{run_id}/task-chains`：查询当前 Run 下连续任务链。
- `GET /api/v1/task-chains/{chain_id}`：查询单个连续任务链及其 Task 投影。
- `POST /api/v1/tasks/{task_id}/plans`：创建手动 `Plan vNext`，可承载 `strategy=manual_orchestration` 的多步串行编排。
- `POST /api/v1/tasks/{task_id}/replan`：重规划兼容入口，语义同创建新的 Plan 版本。

约束：

- 连续任务不直接下发指令；指令仍由 `Action` 通过消息总成下发。
- `TaskChain` 只负责组织多个 Task 的顺序、触发条件和失败策略。
- 每个 Task 保持独立 Plan、Action、Observation、CurrentState 和 Trace 链路。
- 激活新 Plan 时，旧 activePlan 标记为 `Superseded`，历史版本不可覆盖。
- 手动编排任务采用“一个 Task + 多个串行 PlanStep”，循环在前端展开为普通 PlanStep，不在契约中引入嵌套 LoopStep。

### 规则调度推进 PlanStep

`POST /api/v1/simulation-runs/{run_id}/schedule` 当前支持两类用途：

- 任务级调度：未指定 `taskId` 时，从当前 Run 中选择可调度 Task。
- PlanStep 推进：指定 `taskId` 时，从该 Task 的 Active Plan 中选择第一个 `Pending/Ready` 且尚未生成 Action 的 `PlanStep`。

当 `autoIssue=true` 时，调度器会：

- 创建 `Action`。
- 通过消息总成下发 Command。
- 写入 `AgentDecision` 消息。
- 将对应 `PlanStep.status` 更新为 `Issued`。

当前不会自动连续推进全部步骤；后续应在 Observation / CurrentState 闭环稳定后，再由规则调度器或 Agent Service 判断是否自动推进下一步。

### 路径组兼容扩展

本次在不改变既有 API 路径的前提下，新增二维地图分段路径组能力：

- `SiteMap.pathGroups[]`：描述分段路径组。
- `PathEdge.pathGroupId`：标识路径边所属路径组。
- `PathEdge.sequence`：标识路径边在路径组内的顺序。
- `Target Registry targetType=pathGroup`：路径组可被 Trace、Message、Observation 和异常影响范围引用。
- `goto_pose.params.pathGroupId`：可选路径组参数，后端按 `robotCode` 校验机器人是否允许使用该路径组。
- 旧地图若没有 `pathGroups`，平台读取时会兼容生成路径组；多条旧路径边按顺序拆分为多个路径组，前 3 组默认绑定 `robot-001`、`robot-002`、`robot-003`。

前端要求：

- 地图编辑页必须提供路径组选择、新建、状态、颜色和机器人绑定入口。
- 新增路径点时只连接到当前选中路径组，不再连接到全局最后一个路径点。
- 仿真驾驶舱中 `goto_pose` 的 `pathGroupId` 必须以下拉选择方式填写，按当前机器人过滤可用路径组。
- 只读地图按路径组颜色展示路径边，异常事件可通过 `pathGroupId` 定位到路径组。

## Frontend Smoke Test

脚本：

```bash
npm --prefix frontend run smoke
```

默认环境：

- `FRONTEND_BASE=http://localhost:5173`
- `API_BASE=http://localhost:8000`

覆盖：

- 打开 `/`
- 打开 `/simulation`
- 读取场景
- 创建 Run
- 启动 Run
- 下发 `where`
- 查看消息流

## 后续变更规则

- 兼容新增字段：允许，不提升大版本。
- 删除字段、重命名字段、改变字段语义：必须提升契约版本。
- MQTT Topic 变化：必须提升 AsyncAPI 版本。
- 数据库表结构变化：必须追加迁移，不修改历史迁移。
