# 多用户与 Workspace 架构标准

## 文档信息

| 项目 | 内容 |
|---|---|
| 文档类型 | 多用户、Workspace 隔离、控制平面与 Docker 部署标准 |
| 版本 | v1.0 |
| 日期 | 2026-06-22 |
| 适用规模 | 1～20 个同时存在的 Workspace |
| 关联文档 | `实施文档_20260617.md`、`连接与协议标准_20260618.md`、`数据库与数据结构规范_20260617.md`、`仿真驾驶舱规划_20260622.md` |

## 1. 架构结论

平台采用“共享控制平面 + 每 Workspace 独立运行栈”。

核心规则：

- `Workspace` 是部署、网络、数据和权限隔离单元。
- 每个用户创建时自动获得一个默认私有 Workspace。
- 一个用户可拥有多个 Workspace；一个 Workspace 可按权限共享给多个用户。
- 每个 Workspace 独立运行 Frontend、Platform API、Agent Service、MQTT Broker 和 Robot Executor。
- 所有 Workspace 使用同一套源码和不可变 Docker 镜像，禁止复制代码目录形成分叉版本。
- 1～20 Workspace 阶段使用 Docker Compose 模板、反向代理和轻量控制平面，不引入 Kubernetes。

## 2. 总体架构

```text
用户浏览器
    |
Reverse Proxy / API Gateway
    |
共享控制平面
├─ Identity / OIDC
├─ User / Tenant / Workspace
├─ Workspace Provisioner
├─ Route Registry
├─ Image Version Registry
├─ Resource Quota
└─ Global Audit
    |
    ├─ Workspace-A 独立运行栈
    │  ├─ frontend
    │  ├─ platform-api
    │  ├─ agent-service
    │  ├─ mqtt-broker
    │  ├─ robot-executor-*
    │  ├─ redis
    │  ├─ workspace-volume
    │  └─ workspace-network
    |
    └─ Workspace-B 独立运行栈
```

共享基础设施：

- 一个 PostgreSQL 服务器实例。
- 一个反向代理实例。
- 一个身份认证服务。
- 一个控制平面数据库。
- 一个集中日志与备份目录。

## 3. 核心对象

| 对象 | 说明 |
|---|---|
| Tenant | 企业或组织，可选；单机部署可使用默认 Tenant |
| User | 登录用户 |
| Workspace | 独立运行环境、数据和权限边界 |
| WorkspaceMember | 用户与 Workspace 的授权关系 |
| WorkspaceInstance | Workspace 当前运行实例和容器状态 |
| SimulationRun | Workspace 内一次仿真运行 |

层级关系：

```text
Tenant
  └─ User
      └─ Workspace
          └─ SimulationRun
              └─ Task / Plan / Action / Observation / CurrentState / Snapshot / Trace
```

## 4. 每 Workspace 独立服务

| 服务 | 隔离方式 | 说明 |
|---|---|---|
| Frontend | 独立容器和路由 | 使用统一镜像，注入 Workspace 配置 |
| Platform API | 独立容器 | 只连接当前 Workspace 数据库和 Broker |
| Agent Service | 独立容器 | 只处理当前 Workspace 的 Task 和 Plan |
| MQTT Broker | 独立容器 | 保持机器狗 Topic 不变，避免跨 Workspace 冲突 |
| Robot Executor | 独立容器组 | 每个虚拟机器人独立实例 |
| Redis | 独立容器 | 缓存 CurrentState、会话和 WebSocket 数据 |
| Database | 独立 Database 和 Role | 位于共享 PostgreSQL 服务器中 |
| Volume | 独立目录或 Docker Volume | 地图、导出、日志、Snapshot |
| Network | 独立 Docker Network | 禁止 Workspace 间容器互访 |

前端独立容器主要用于配置和发布隔离；安全隔离的核心仍是 API、数据库、Broker、网络和凭证。

## 5. 共享控制平面

新增 `control-plane-api`，不参与机器人指令和仿真决策。

职责：

- Tenant、User、Workspace、成员和角色管理。
- 创建、启动、停止、恢复、归档和删除 Workspace。
- 生成 Workspace Docker Compose 参数和 Secret。
- 创建 PostgreSQL Database、Role 和初始 Schema。
- 分配 HTTP 路由、MQTT 端口、网络和 Volume。
- 检查 Workspace 健康状态。
- 管理镜像版本、升级批次和失败回滚。
- 管理 CPU、内存、磁盘、机器人数量和运行时长配额。
- 记录全局审计日志。

控制平面不允许：

- 直接发布机器狗 MQTT command。
- 直接修改 Workspace 的 CurrentState。
- 代替 Agent Service 进行任务规划。

## 6. Workspace 生命周期

```text
Creating
→ Provisioning
→ Starting
→ Running
→ Stopping
→ Stopped
→ Archived
→ Deleted
```

异常状态：

```text
ProvisionFailed
StartFailed
Degraded
UpgradeFailed
```

标准流程：

1. 用户创建 Workspace。
2. 控制平面验证配额和名称唯一性。
3. 创建 Database、Role、Volume 和 Docker Network。
4. 分配 Frontend/API 路由和 MQTT 端口。
5. 使用统一镜像启动 Workspace Stack。
6. 执行 API、MQTT、数据库和执行体健康检查。
7. 全部通过后将 Workspace 标记为 `Running`。
8. 停止时保留 Database、Volume 和配置，释放运行资源。

## 7. 路由与端口

HTTP 推荐使用反向代理统一暴露 80/443：

```text
https://{workspaceSlug}.sim.local
https://api-{workspaceSlug}.sim.local
```

不具备局域网 DNS 时可使用路径路由：

```text
http://{PUBLIC_HOST}/w/{workspaceSlug}
http://{PUBLIC_HOST}/api/w/{workspaceSlug}
```

MQTT 使用独立 Broker 和端口池：

```text
Workspace-01: 18830
Workspace-02: 18831
...
Workspace-20: 18849
```

生产 TLS 端口池可从 `28830～28849` 分配，或后续使用支持 MQTT SNI 的入口网关。

容器内部端口保持固定：

| 服务 | 容器端口 |
|---|---:|
| Frontend | 80 |
| Platform API | 8000 |
| Agent Service | 8100 |
| MQTT Broker | 1883 / 8883 |
| Redis | 6379 |
| PostgreSQL | 5432 |

## 8. 标准环境变量

每个 Workspace 必须注入：

```text
TENANT_ID
OWNER_USER_ID
WORKSPACE_ID
WORKSPACE_SLUG
WORKSPACE_NAME
PUBLIC_HOST
FRONTEND_PUBLIC_URL
API_PUBLIC_URL
MQTT_PUBLIC_HOST
MQTT_PUBLIC_PORT
MQTT_INTERNAL_HOST
MQTT_INTERNAL_PORT
MQTT_USERNAME
MQTT_PASSWORD_FILE
DATABASE_URL_FILE
REDIS_URL
WORKSPACE_DATA_PATH
IMAGE_VERSION
```

密码、数据库连接串和证书只能通过 Secret/File 注入，不写入 Compose 文件、日志或导出包。

## 9. 数据隔离

1～20 Workspace 推荐：

- 共享一个 PostgreSQL Server。
- 控制平面使用独立 `control_plane` Database。
- 每个 Workspace 使用独立 Database，例如 `workspace_{id}`。
- 每个 Workspace 使用独立数据库 Role，禁止访问其他 Database。
- 每个 Workspace 独立 Redis 容器。
- 每个 Workspace 独立 MQTT Broker。
- 每个 Workspace 独立文件 Volume。

所有 Workspace 业务表必须包含：

```text
workspace_id
run_id（运行域数据）
created_at
updated_at
```

控制平面表额外包含 `tenant_id` 和 `user_id`。

## 10. MQTT 隔离

每 Workspace 使用独立 Broker，因此机器狗协议保持：

```text
factory/dogs/{robotCode}/command
factory/dogs/{robotCode}/result
```

不在 Topic 或 Payload 中增加 `tenantId/workspaceId`，避免破坏真实机器狗终版协议。

隔离规则：

- `robotCode` 在 Workspace 内唯一。
- 真实机器狗同一时间只能绑定一个 Workspace。
- 机器人使用独立 MQTT 用户名或证书。
- ACL 只允许机器人订阅自身 command、发布自身 result。
- Workspace 切换时必须撤销旧凭证并重新绑定。

## 11. 身份与权限

身份认证采用统一 OIDC/JWT。1～20 Workspace 可使用 Keycloak 单实例。

JWT 必须包含：

```text
tenantId
userId
workspaceId
roles
```

角色：

| 角色 | 权限 |
|---|---|
| owner | Workspace 全部管理权限 |
| operator | Task、Run、Action、事件和导出操作 |
| viewer | 只读地图、状态、Trace 和消息 |
| auditor | 只读审计、Snapshot、Trace 和导出记录 |
| agent | Agent Service 服务身份 |
| device | MQTT 机器人设备身份 |

## 12. 资源建议

单 Workspace 基础资源建议：

| 服务 | CPU | 内存 |
|---|---:|---:|
| Frontend | 0.1 Core | 64～128 MB |
| Platform API | 0.5 Core | 256～512 MB |
| Agent Service | 0.5 Core | 256～1024 MB，不含本地大模型 |
| MQTT Broker | 0.1 Core | 64～128 MB |
| Redis | 0.1 Core | 64～128 MB |
| Robot Executor | 0.1～0.25 Core/实例 | 64～128 MB/实例 |

20 Workspace 建议宿主机最低配置：

- 16 Core CPU。
- 32 GB RAM。
- 500 GB SSD。
- 日志、Snapshot 和导出目录单独设置容量限制。

具体配置以并发机器人数量、消息频率和 Agent 模型部署方式压测后确定。

## 13. 镜像与升级

统一维护镜像：

```text
simulation-frontend:{version}
platform-api:{version}
agent-service:{version}
robot-executor:{version}
```

升级流程：

1. 选择一个测试 Workspace 灰度升级。
2. 执行数据库 Migration。
3. 验证健康、Task、MQTT、Snapshot 和 Trace。
4. 按批次升级其他 Workspace。
5. 失败时恢复旧镜像并执行兼容回滚方案。

禁止直接在运行容器内修改文件。

## 14. 控制平面 API 规划

```text
POST /api/control/v1/workspaces
GET  /api/control/v1/workspaces
GET  /api/control/v1/workspaces/{workspaceId}
POST /api/control/v1/workspaces/{workspaceId}/start
POST /api/control/v1/workspaces/{workspaceId}/stop
POST /api/control/v1/workspaces/{workspaceId}/upgrade
GET  /api/control/v1/workspaces/{workspaceId}/health
POST /api/control/v1/workspaces/{workspaceId}/mqtt-credentials/rotate
PUT  /api/control/v1/workspaces/{workspaceId}/members
GET  /api/control/v1/workspaces/{workspaceId}/usage
```

Workspace 业务 API 继续使用 `/api/v1`，只能访问当前 Workspace 数据。

## 15. 日志、备份与审计

- 日志必须包含 `workspaceId`、`runId`、`traceId`。
- Workspace 日志分别落盘并设置保留周期。
- Database 每日备份，关键演示前执行手动备份。
- Snapshot、地图配置和导出文件进入 Workspace 独立目录。
- 删除 Workspace 前生成最终归档包并设置确认流程。
- 控制平面操作和 Workspace 业务操作分别审计。

## 16. 安全边界

- 默认只暴露反向代理和 Workspace MQTT 端口。
- PostgreSQL、Redis 和容器内部 API 不直接暴露到局域网。
- Workspace Network 之间禁止路由。
- API 必须校验 JWT 中的 `workspaceId`。
- MQTT 必须启用身份认证和 ACL；真实设备阶段启用 TLS。
- Secret 不允许进入前端、日志、Snapshot 和导出包。

## 17. 本阶段不采用的方案

- 不复制 20 套代码仓库。
- 不为每个 Workspace 启动独立 PostgreSQL 容器。
- 不引入 Kubernetes、Service Mesh 或 Kafka。
- 不实现跨宿主机高可用编排。
- 不允许用户自行修改 Docker Compose 或宿主机端口。

## 18. 验收标准

- 可创建并同时运行至少 5 个 Workspace，架构上支持扩展到 20 个。
- 每个 Workspace 有独立 Frontend、Platform API、Broker、Redis、Executor、Network 和 Volume。
- 两个 Workspace 使用相同 `robotCode` 时互不影响。
- 用户不能访问未授权 Workspace 的页面、API、MQTT、日志和导出文件。
- 停止 Workspace 后数据可恢复，重新启动后地图、Task、Snapshot 和 Trace 保持一致。
- 一个镜像版本可统一升级多个 Workspace。
- 控制平面可查看实例状态、资源使用和健康信息。
