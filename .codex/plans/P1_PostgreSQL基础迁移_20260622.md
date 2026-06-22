# P1 PostgreSQL 基础迁移

## 任务背景

当前平台虽然启动了 PostgreSQL 容器，但 Platform API 的地图、草稿、机器人、消息和审计仍使用 `data/state.json`。按照 v2.0 路线图，必须先完成 P1 主存储迁移，才能进入 P3 SimulationRun、Task 和仿真驾驶舱。

## 用户需求

- 严格按照规划执行。
- 当前先实施 P1 PostgreSQL Migration 和正式数据访问层。
- 不提前实现 P3 驾驶舱页面。

## 当前代码现状

- `JsonStore` 是 Platform API 和 MQTT Bridge 的主存储。
- Docker Compose 中 PostgreSQL 未被 Platform API 使用。
- 后端未安装 SQLAlchemy、Alembic 和 Psycopg 运行依赖。
- 当前自动化测试通过 JSON Store 运行。
- `data/state.json` 包含现有地图、机器人、消息和审计历史。

## 涉及文件

- `backend/requirements.txt`
- `backend/Dockerfile`
- `backend/alembic.ini`
- `backend/alembic/env.py`
- `backend/alembic/versions/20260622_0001_p1_core.py`
- `backend/app/database.py`
- `backend/app/db_models.py`
- `backend/app/database_store.py`
- `backend/app/store_factory.py`
- `backend/app/main.py`
- `backend/app/mqtt_bridge.py`
- `backend/tests/test_database_store.py`
- `docker-compose.yml`
- `.env.example`
- `README.md`
- `.codex/docs/开发规划实施路线图_20260617.md`

## 风险点

- 首次迁移必须保留当前 `state.json` 中的地图、机器人和消息。
- Migration 必须可重复执行，并能在空库初始化。
- MQTT 回调线程与 FastAPI 请求可能并发写入数据库。
- 测试不能依赖开发机固定 PostgreSQL 状态。
- 数据库不可用时不能静默回退到 JSON，避免双主存储。

## 待确认问题

- 无阻塞问题。Docker 环境强制使用 PostgreSQL；测试环境允许显式使用 JSON Store 或独立测试数据库，但不得自动静默回退。

## 执行计划

- [x] 增加 SQLAlchemy、Alembic、Psycopg 依赖。
- [x] 建立数据库 Engine、Session 和 ORM 模型。
- [x] 建立 P1 核心 Alembic Migration。
- [x] 实现 DatabaseStore，覆盖当前 Store 接口。
- [x] 实现首次 JSON 数据导入和幂等种子初始化。
- [x] 增加 Store Factory，Docker 强制选择 PostgreSQL。
- [x] 更新 Dockerfile 和 Compose 启动迁移流程。
- [x] 增加 DatabaseStore 自动化测试。
- [x] 执行 Migration、Pytest、前端构建和 Docker 联调。
- [x] 更新路线图实施记录。

## 验证方式

- `python -m py_compile` 检查数据库模块。
- `python -m pytest backend/tests`。
- 对空 PostgreSQL 执行 `alembic upgrade head`。
- 重复执行 `alembic upgrade head`。
- `docker compose up -d --build`。
- `GET /api/v1/health` 确认 storage backend 为 PostgreSQL。
- 保存、校验、发布地图草稿。
- 下发 command 并确认 command/result 写入 PostgreSQL。
- 重启 Platform API 后数据保持。

## 回滚方案

- 停止 Platform API。
- 恢复旧镜像和 Compose 配置。
- 保留 PostgreSQL Volume，不删除迁移数据。
- 显式设置 `STORE_BACKEND=json` 可临时读取原 `state.json`，不得双写。

## 实施结果

- 完成日期：2026-06-22。
- Migration 版本：`20260622_0001`。
- PostgreSQL Schema：`config`、`runtime`、`message`、`audit`、`export`。
- 自动化测试：`python -m pytest backend/tests -q`，9 项通过。
- 容器验证：前端生产构建、API 启动前迁移、PostgreSQL 健康检查均通过。
- 业务验证：地图草稿保存/校验/发布、`where` command/result Trace、服务重启持久化均通过。
- 范围结论：本计划的 PostgreSQL 主存储迁移已完成；P1 的 Redis 缓存和备份恢复说明仍按路线图后续实施。
