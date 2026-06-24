# P3 Action 参数、Trace 与通信补充规范

## 1. 本次落地范围

本文件记录 2026-06-23 本轮执行后的接口与协议补充，适用于仿真驾驶舱、消息总成、虚拟机器人执行体和后续真实机器人网关替换。

已落地能力：
- Action 标准参数规格接口：`GET /api/v1/action-command-specs`
- Action 创建参数校验：`POST /api/v1/actions`
- Task Trace 查询：`GET /api/v1/tasks/{task_id}/trace`
- Action Trace 查询：`GET /api/v1/actions/{action_id}/trace`
- Trace 图查询：`GET /api/v1/traces/{trace_id}/graph`
- Run 消息统计：`GET /api/v1/simulation-runs/{run_id}/message-metrics`
- 控制台异常注入通过 `sim/{env}/{siteId}/broadcast/event` 广播给虚拟执行体
- 虚拟执行体支持 `goto_pose/where/stop/pick/place/load/unload/inspect/charge/wait`

## 2. Action 参数模型

### 2.1 标准命令清单

```text
goto_pose
where
stop
pick
place
load
unload
inspect
charge
wait
```

### 2.2 参数规则

| command | 必填参数 | 默认参数 | 说明 |
|---|---|---|---|
| `goto_pose` | `x`, `y` | `z=0`, `yaw=0`, `speed=1.0`, `tolerance=50` | 移动到二维/姿态点位 |
| `where` | 无 | `queryMode=pose` | 查询模式：`pose/state/full` |
| `stop` | 无 | `stopScope=current_action`, `reason=manual_stop` | 停止范围：`current_action/task/robot` |
| `pick` | `targetId` | `durationMinMs=3000`, `durationMaxMs=5000` | 抓取目标 |
| `place` | `targetId` | `durationMinMs=3000`, `durationMaxMs=5000` | 放置目标 |
| `load` | `stationId` | `durationMinMs=5000`, `durationMaxMs=8000` | 装载 |
| `unload` | `stationId` | `durationMinMs=5000`, `durationMaxMs=8000` | 卸载 |
| `inspect` | `targetId` | `durationMinMs=4000`, `durationMaxMs=7000` | 巡检/检测 |
| `charge` | 无 | `targetBattery=95`, `durationMinMs=10000`, `durationMaxMs=15000` | 充电 |
| `wait` | 无 | `durationMinMs=1000`, `durationMaxMs=3000` | 等待 |

### 2.3 校验规则

- `goto_pose.x/y` 缺失时，`POST /api/v1/actions` 返回 `400`。
- `number` 类型字段会在后端归一化为数字。
- `select` 类型字段必须命中枚举值。
- `durationMinMs >= 0` 且 `durationMaxMs >= durationMinMs`。
- `speed > 0`。
- `tolerance >= 0`。

## 3. REST 接口补充

### 3.1 获取动作规格

```http
GET /api/v1/action-command-specs
```

返回每个 command 的 `label/required/defaults/fields`，前端高级指令表单必须以该接口为准动态渲染，不允许写死参数字段。

### 3.2 Trace 查询

```http
GET /api/v1/tasks/{task_id}/trace
GET /api/v1/actions/{action_id}/trace
GET /api/v1/traces/{trace_id}/graph
```

`trace graph` 返回：

```json
{
  "traceId": "TRACE-xxx",
  "status": "Open",
  "nodes": [
    {
      "id": "SPAN-xxx",
      "label": "action.created",
      "type": "Action",
      "entityId": "ACT-xxx",
      "status": "Completed",
      "startedAt": "2026-06-23T00:00:00+00:00"
    }
  ],
  "edges": [
    { "from": "SPAN-1", "to": "SPAN-2", "type": "sequence" }
  ]
}
```

### 3.3 消息统计

```http
GET /api/v1/simulation-runs/{run_id}/message-metrics
```

返回：
- `messageCount`
- `categoryCounts`
- `eventCounts`
- `duplicateCount`
- `timeoutCount`
- `errorCount`
- `ackDelayMs.count/avg/max`

## 4. MQTT 通信补充

### 4.1 Command Topic

```text
factory/dogs/{robotCode}/command
```

平台只通过消息总成下发 MQTT command，前端不直连 MQTT，不直接操作执行体。

### 4.2 Result Topic

```text
factory/dogs/{robotCode}/result
```

虚拟执行体和真实机器人网关必须遵守同一 result 协议，便于后续替换执行体。

### 4.3 新增 Result Event

| event | 分类 | 说明 |
|---|---|---|
| `action.started` | Event | 动作开始 |
| `action.progress` | Telemetry | 动作执行进度 |
| `action.succeeded` | Event | 动作成功 |
| `action.failed` | Alert | 动作失败 |
| `path.blocked` | Alert | 路径阻塞 |
| `fault.recovered` | Event | 故障恢复 |

### 4.4 异常广播 Topic

```text
sim/{env}/{siteId}/broadcast/event
```

控制台事件注入后，平台会同时：
- 写入 `MessageRecord`
- 沉淀为 `Observation`
- 通过 broadcast topic 通知虚拟执行体

广播封装保留可索引字段：
- `runId`
- `taskId`
- `robotCode`
- `traceId`
- `event`
- `payload.eventType`
- `payload.targetType`
- `payload.targetId`

## 5. 虚拟执行体语义

### 5.1 动作耗时

执行体根据命令 profile 或参数中的 `durationMinMs/durationMaxMs` 随机生成实际耗时。

示例：

```json
{
  "command": "pick",
  "params": {
    "targetId": "box-001",
    "durationMinMs": 3000,
    "durationMaxMs": 5000
  }
}
```

### 5.2 状态上报

每个动作至少包含：

```text
command.accepted
action.started
action.progress
action.succeeded | action.failed
task.succeeded | task.failed | task.stopped
```

`where` 使用：

```text
command.accepted
where.result
```

### 5.3 异常响应

| 注入事件 | 执行体行为 |
|---|---|
| `robot.offline` | 进入 `Offline`，拒绝后续非 `stop` command，并发布 `device.offline` |
| `fault.recovered` | 清除 `Offline/path_blocked/fail_next_action`，恢复 Idle 或当前执行状态 |
| `action.failed` | 下一次或当前动作进入失败路径 |
| `path.blocked` / `resource.blocked` | 标记路径阻塞，当前动作失败并发布 `path.blocked/action.failed/task.failed` |

## 6. 前端驾驶舱约束

- Task 列表可选中。
- 当前 Task 的 Plan 步骤按 `selectedPlanStepId` 或 `selectedAction.planStepId` 高亮。
- Action 队列按当前 Task 过滤。
- 点击 Action 后联动：
  - 选中 Command message
  - 查询 Action Trace
  - 查询 Trace Graph
- 高级指令参数表单必须来源于 `GET /api/v1/action-command-specs`。
- sandbox replay 暂时禁用，并标记其会写入当前 Workspace 数据。

## 7. 验证记录

已通过：

```text
python -m pytest backend\tests
npm --prefix frontend run build
```

