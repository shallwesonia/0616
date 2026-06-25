# 前端 Smoke Test 清单 v0.3.0-mvp-baseline

## 目标

验证 MVP 基线下前端核心入口和仿真驾驶舱最小闭环可用。

## 执行命令

```bash
npm --prefix frontend run smoke
```

可选环境变量：

```bash
FRONTEND_BASE=http://localhost:5173
API_BASE=http://localhost:8000
```

## 检查项

| 序号 | 检查项 | 通过标准 |
|---|---|---|
| 1 | 打开 `/` | HTTP 2xx，首页可返回 |
| 2 | 打开 `/simulation` | HTTP 2xx，驾驶舱页面可返回 |
| 3 | 读取场景 | `GET /api/v1/scenarios` 返回非空数组 |
| 4 | 创建 Run | `POST /api/v1/simulation-runs` 返回 `runId` |
| 5 | 启动 Run | `POST /api/v1/simulation-runs/{runId}/start` 成功 |
| 6 | 下发 `where` | `POST /api/v1/actions` 返回 `actionId` 和 `commandId` |
| 7 | 查看消息流 | `GET /api/v1/simulation-runs/{runId}/messages` 返回数组 |

## 失败处理

- `/` 或 `/simulation` 失败：优先检查前端服务、反向代理和端口。
- 场景读取失败：检查 Platform API、数据库初始化和 CORS。
- 创建 Run 失败：检查默认场景、地图和机器人配置。
- 下发 `where` 失败：检查机器人配置、Action 参数规格和 MQTT 桥接状态。
- 消息流为空不一定代表失败；在 MQTT 未连接时，平台仍应记录 command 消息。
