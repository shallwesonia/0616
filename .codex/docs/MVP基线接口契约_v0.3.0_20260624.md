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
