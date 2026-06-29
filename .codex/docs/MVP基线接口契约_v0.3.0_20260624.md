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
| Task/Action | `POST /api/v1/simulation-runs/{run_id}/tasks`、`POST /api/v1/actions` |
| 规则调度 / AgentDecision | `POST /api/v1/simulation-runs/{run_id}/schedule` |
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
- Simulation Run / Task / Plan / Action / Observation / CurrentState / Snapshot / Trace
- AgentDecision：当前通过 `agentDecision` 消息和 Trace 引用固化，后续可升级为独立表。

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
