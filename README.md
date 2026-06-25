# 具身智能业务流程仿真平台 MVP

当前基线版本：`v0.3.0-mvp-baseline`。

本工程按 `.codex/docs` 中的实施文档落地：二维地图环境编辑、平台 API、WebSocket 实时展示、MQTT 消息桥接、独立虚拟机器人执行体、配置导入导出、日志导出和 Docker Compose 部署。

## 本地运行

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

```bash
pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

运行后端测试：

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests
```

运行前端 smoke test：

```bash
npm --prefix frontend run smoke
```

```bash
pip install -r robot-executor/requirements.txt
python robot-executor/app/main.py
```

## Docker 运行

```bash
docker compose up -d --build
```

Docker 环境默认使用 PostgreSQL 主存储，容器启动时先自动执行 `alembic upgrade head`。首次空库启动会导入现有 `data/state.json`；后续启动不会重复导入。`STORE_BACKEND=json` 仅用于显式本地兼容，不与 PostgreSQL 双写。

Redis 用于最新机器人状态、最近消息缓冲和 Workspace 事件发布。Redis 是可重建辅助缓存，故障时 API 与 WebSocket 回退 PostgreSQL。数据库备份写入 `data/backups`，标准流程见 `.codex/docs/数据库备份恢复与初始化_20260622.md`。

Docker Compose 默认启动 3 个独立虚拟机器狗执行体：`robot-001`、`robot-002`、`robot-003`。三者共用当前 Workspace MQTT Broker，但使用各自的 `ROBOT_CODE` 和 MQTT clientId，平台按 `robotCode` 聚合状态和下发 Action。

本机访问：

- 前端：`http://localhost:5173`
- API：`http://localhost:8000/docs`
- 连接信息：`http://localhost:8000/api/v1/connections`
- MQTT：`localhost:18830`

局域网访问：

1. 复制 `.env.example` 为 `.env`。
2. 将 `PUBLIC_HOST` 改为宿主机局域网 IP。
3. 执行 `docker compose up -d --build`。
4. 同一局域网设备访问：
   - 前端：`http://{PUBLIC_HOST}:5173`
   - API：`http://{PUBLIC_HOST}:8000/docs`
   - MQTT：`{PUBLIC_HOST}:18830`

## 核心边界

- 前端负责二维环境编辑和运行态展示，不直接下发机器人控制指令。
- 平台 API 负责配置草稿、校验、发布、导入导出、消息记录、Target Registry、机器人配置、执行体管理和 MQTT 桥接。
- 规则调度接口先输出可追踪的 AgentDecision，并通过平台 API 创建 Action，不允许 Agent 直连 MQTT。
- 平台 API 的地图、草稿、机器人状态、消息、导出任务和审计记录由 PostgreSQL 持久化，并按 `WORKSPACE_ID` 隔离。
- 虚拟机器狗执行体是独立服务，只依赖 MQTT 契约；当前默认部署 3 个执行体实例，后续可逐台替换为真实机器狗网关。
- MQTT 对外接口按机器狗 command/result 标准：`factory/dogs/{robotCode}/command`、`factory/dogs/{robotCode}/result`。
- 当前动作集支持 `goto_pose`、`stop`、`where`、`pick`、`place`、`load`、`unload`、`inspect`、`charge`、`wait`。
- 动作耗时使用动作配置区间随机生成，默认 `goto_pose` 为 30-35 秒。

## 指令示例

```bash
curl -X POST http://localhost:8000/api/v1/commands ^
  -H "Content-Type: application/json" ^
  -d "{\"robotCode\":\"robot-001\",\"command\":\"goto_pose\",\"params\":{\"x\":760,\"y\":420,\"z\":0,\"yaw\":0}}"
```

## 文档

- `.codex/docs/实施文档_20260617.md`
- `.codex/docs/通信规范_MQTT_API_20260617.md`
- `.codex/docs/数据库与数据结构规范_20260617.md`
- `.codex/docs/数据库备份恢复与初始化_20260622.md`
- `.codex/docs/连接与协议标准_20260618.md`
- `.codex/docs/机器狗MQTT接口标准_20260618.md`
- `.codex/docs/多用户与Workspace架构标准_20260622.md`
- `.codex/docs/仿真驾驶舱规划_20260622.md`
- `.codex/docs/MVP基线接口契约_v0.3.0_20260624.md`
- `.codex/docs/前端SmokeTest清单_v0.3.0_20260624.md`
- `docs/contracts/openapi.json`
- `docs/contracts/asyncapi.yaml`
