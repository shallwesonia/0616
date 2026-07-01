from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .mqtt_bridge import PlatformMqttBridge
from .mqtt_contract import MQTT_CONTRACT
from .hub_client import HubClient, HubIntegrationService, hub_mqtt_subscription_info
from .schemas import (
    AgentDecision,
    BatchTaskCreate,
    BatchTaskResponse,
    ACTION_COMMAND_SPECS,
    ActionCommandSpec,
    CommandCreate,
    CommandResponse,
    ConsoleEventCreate,
    ConsoleEventResponse,
    DraftResponse,
    ExecutorInstance,
    ExecutorInstanceCreate,
    ExecutorLogResponse,
    ExecutorTransitionResponse,
    ExportCreate,
    ExportResponse,
    ActionCreate,
    CurrentState,
    HubIdMapping,
    HubIntegrationStatus,
    MapPublishHubSync,
    MapPublishResponse,
    HubSyncRequest,
    HubSyncResponse,
    MapImportResponse,
    MapDraftCreate,
    MessageRecord,
    MessageReplayCreate,
    MessageReplayResponse,
    Observation,
    RobotConfig,
    RobotConfigCreate,
    RobotConfigUpdate,
    RobotCreate,
    RobotState,
    RuleScheduleRequest,
    RuleScheduleResponse,
    ScenarioSummary,
    ScenarioValidationResponse,
    SiteMap,
    SimulationAction,
    SimulationEventCreate,
    SimulationEventRecoveryCreate,
    SimulationPlan,
    SimulationPlanCreate,
    SimulationRun,
    SimulationRunCreate,
    SimulationTask,
    SimulationTaskCreate,
    Snapshot,
    SnapshotCreate,
    TaskChain,
    TaskChainCreate,
    TargetRegistryItem,
    TargetRegistryItemCreate,
    TargetRegistryItemUpdate,
    TaskFromTemplateCreate,
    TaskTemplate,
    TraceResponse,
    ValidationResponse,
    action_command_names,
    new_id,
    protocol_id,
    utc_now,
    validate_action_params,
)
from .store import EXPORT_DIR
from .store_factory import create_store
from .hub_compat import (
    HubActionCreate,
    action_to_hub_response,
    create_hub_action,
    find_hub_object,
    register_hub_compat_routes,
    task_to_hub_response,
)


store = create_store()
bridge = PlatformMqttBridge(store)


def hub_service() -> HubIntegrationService:
    return HubIntegrationService(store, HubClient.from_env())


def _audit_hub_map_publish(map_id: str, hub_sync: MapPublishHubSync) -> None:
    if hasattr(store, "append_audit"):
        store.append_audit(
            "hub.map_publish.sync",
            "map",
            map_id,
            after=hub_sync.model_dump(),
        )


def sync_hub_after_map_publish(scenario_id: str, map_id: str) -> MapPublishHubSync:
    service = hub_service()
    status = service.status()
    if not status.enabled:
        hub_sync = MapPublishHubSync(enabled=False, status="skipped", reason="Hub sync is disabled")
        _audit_hub_map_publish(map_id, hub_sync)
        return hub_sync
    if status.status != "ok":
        hub_sync = MapPublishHubSync(enabled=True, status="failed", reason=status.error or "Hub status is not ok")
        _audit_hub_map_publish(map_id, hub_sync)
        return hub_sync

    scene_response = service.sync_scene(scenario_id, force=True)
    entities_response = service.sync_entities(scenario_id, force=True) if scene_response.ok else None
    if scene_response.ok and entities_response and entities_response.ok:
        result_status = "synced"
    elif scene_response.ok or (entities_response is not None and entities_response.ok):
        result_status = "partial"
    else:
        result_status = "failed"
    reason = None
    if not scene_response.ok:
        reason = scene_response.error or "Hub scene sync failed"
    elif entities_response is None:
        reason = "Hub entity sync skipped because scene sync failed"
    elif not entities_response.ok:
        reason = entities_response.error or "Hub entity sync failed"

    hub_sync = MapPublishHubSync(
        enabled=True,
        status=result_status,
        reason=reason,
        scene=scene_response,
        entities=entities_response,
    )
    _audit_hub_map_publish(map_id, hub_sync)
    return hub_sync


@asynccontextmanager
async def lifespan(_: FastAPI):
    bridge.start()
    yield
    bridge.stop()


app = FastAPI(
    title="Embodied Workflow Simulation Platform API",
    version="0.3.0-mvp-baseline",
    description="2D environment configuration, message hub facade, Target Registry, robot/executor management and simulation API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
def health() -> dict:
    storage = store.storage_health()
    runtime_cache = store.runtime_cache_health()
    mqtt = bridge.health()
    last_heartbeat_at = store.last_heartbeat_at()
    recent = store.recent_runtime_summary()
    executor_status = "ok" if last_heartbeat_at else "unknown"
    component_statuses = [storage["status"], runtime_cache["status"], mqtt["status"], executor_status]
    overall = "ok" if all(status in {"ok", "disabled"} for status in component_statuses) else "degraded"
    return {
        "status": overall,
        "time": utc_now(),
        "components": {
            "api": {"status": "ok"},
            "storage": storage,
            "runtimeCache": runtime_cache,
            "mqttBridge": mqtt,
            "virtualExecutor": {
                "status": executor_status,
                "lastHeartbeatAt": last_heartbeat_at,
            },
        },
        "recent": recent,
    }


@app.get("/api/v1/connections")
def get_connections() -> dict:
    public_host = os.getenv("PUBLIC_HOST", "localhost")
    frontend_port = int(os.getenv("FRONTEND_PORT", "5173"))
    api_port = int(os.getenv("API_PORT", "8000"))
    mqtt_public_port = int(os.getenv("MQTT_PUBLIC_PORT", "18830"))
    topic_prefix = "factory/dogs"
    return {
        "schemaVersion": "1.0",
        "publicHost": public_host,
        "services": {
            "frontend": {
                "protocol": "HTTP",
                "url": f"http://{public_host}:{frontend_port}",
                "lanPort": frontend_port,
            },
            "api": {
                "protocol": "HTTP REST",
                "baseUrl": f"http://{public_host}:{api_port}/api/v1",
                "openApiUrl": f"http://{public_host}:{api_port}/docs",
                "lanPort": api_port,
            },
            "websocket": {
                "protocol": "WebSocket",
                "url": f"ws://{public_host}:{frontend_port}/ws/v1/sessions/session-local",
                "backendUrl": f"ws://{public_host}:{api_port}/ws/v1/sessions/session-local",
            },
            "mqtt": {
                "protocol": "MQTT 3.1.1",
                "host": public_host,
                "port": mqtt_public_port,
                "internalHost": os.getenv("MQTT_HOST", "mqtt-broker"),
                "internalPort": int(os.getenv("MQTT_PORT", "1883")),
                "topicPrefix": topic_prefix,
                "commandTopic": f"{topic_prefix}/{{robotCode}}/command",
                "resultTopic": f"{topic_prefix}/{{robotCode}}/result",
                "supportedCommands": action_command_names(),
                "resultEvents": [
                    "command.accepted",
                    "command.rejected",
                    "action.started",
                    "action.progress",
                    "action.succeeded",
                    "action.failed",
                    "task.started",
                    "task.succeeded",
                    "task.failed",
                    "task.stopped",
                    "task.timeout",
                    "pose.updated",
                    "where.result",
                    "where.failed",
                    "device.offline",
                    "path.blocked",
                    "fault.recovered",
                ],
            },
        },
        "rules": [
            "frontend must not connect directly to robot command topics",
            "commands must be created through /api/v1/commands or agent service",
            "virtual and real robot executors must implement the same MQTT contract",
            "command messages must not be retained",
            "robot command topic must be factory/dogs/{robotCode}/command",
            "robot result topic must be factory/dogs/{robotCode}/result",
        ],
    }


@app.get("/api/v1/integrations/hub/status", response_model=HubIntegrationStatus)
def get_hub_integration_status() -> HubIntegrationStatus:
    return hub_service().status()


@app.get("/api/v1/integrations/hub/mqtt-subscription")
def get_hub_mqtt_subscription() -> dict[str, Any]:
    return hub_mqtt_subscription_info()


@app.get("/api/v1/integrations/hub/mappings", response_model=list[HubIdMapping])
def list_hub_mappings(limit: int = 200) -> list[HubIdMapping]:
    if not hasattr(store, "list_hub_mappings"):
        raise HTTPException(status_code=501, detail="Hub ID mappings require database store")
    return store.list_hub_mappings(limit=limit)


@app.post("/api/v1/integrations/hub/sync/scenes/{scenario_id}", response_model=HubSyncResponse)
def sync_hub_scene(scenario_id: str, request: HubSyncRequest | None = None) -> HubSyncResponse:
    payload = request or HubSyncRequest()
    return hub_service().sync_scene(scenario_id, force=payload.force)


@app.post("/api/v1/integrations/hub/sync/entities/{scenario_id}", response_model=HubSyncResponse)
def sync_hub_entities(scenario_id: str, request: HubSyncRequest | None = None) -> HubSyncResponse:
    payload = request or HubSyncRequest()
    return hub_service().sync_entities(scenario_id, force=payload.force)


@app.post("/api/v1/integrations/hub/sync/runs/{run_id}", response_model=HubSyncResponse)
def sync_hub_run_graph(run_id: str, request: HubSyncRequest | None = None) -> HubSyncResponse:
    payload = request or HubSyncRequest()
    return hub_service().sync_run_graph(run_id, force=payload.force)


@app.post("/api/v1/integrations/hub/sync/tasks/{task_id}", response_model=HubSyncResponse)
def sync_hub_task(task_id: str, request: HubSyncRequest | None = None) -> HubSyncResponse:
    payload = request or HubSyncRequest()
    return hub_service().sync_task(task_id, force=payload.force)


@app.post("/api/v1/integrations/hub/sync/plans/{plan_id}", response_model=HubSyncResponse)
def sync_hub_plan(plan_id: str, request: HubSyncRequest | None = None) -> HubSyncResponse:
    payload = request or HubSyncRequest()
    return hub_service().sync_plan(plan_id, force=payload.force)


@app.post("/api/v1/integrations/hub/sync/actions/{action_id}", response_model=HubSyncResponse)
def sync_hub_action(action_id: str, request: HubSyncRequest | None = None) -> HubSyncResponse:
    payload = request or HubSyncRequest()
    return hub_service().sync_action(action_id, force=payload.force)


@app.get("/api/v1/maps/current", response_model=SiteMap)
def get_current_map() -> SiteMap:
    return store.current_map()


@app.post("/api/v1/maps/{map_id}/drafts", response_model=DraftResponse)
def create_map_draft(map_id: str, request: MapDraftCreate) -> DraftResponse:
    if map_id != request.map.id:
        raise HTTPException(status_code=400, detail="map id in path and body must match")
    draft_id = store.save_draft(request.map)
    return DraftResponse(draftId=draft_id, map=request.map)


@app.post("/api/v1/maps/{map_id}/drafts/{draft_id}/validate", response_model=ValidationResponse)
def validate_map_draft(map_id: str, draft_id: str) -> ValidationResponse:
    draft = store.draft_map(draft_id)
    if draft is None or draft.id != map_id:
        raise HTTPException(status_code=404, detail="draft not found")
    issues = store.validate_map(draft)
    return ValidationResponse(ok=len(issues) == 0, issues=issues)


@app.post("/api/v1/maps/{map_id}/drafts/{draft_id}/publish", response_model=MapPublishResponse)
def publish_map_draft(map_id: str, draft_id: str) -> MapPublishResponse:
    draft = store.draft_map(draft_id)
    if draft is None or draft.id != map_id:
        raise HTTPException(status_code=404, detail="draft not found")
    issues = store.validate_map(draft)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})
    published_result = store.publish_draft(draft_id)
    if published_result is None:
        raise HTTPException(status_code=404, detail="draft not found")
    published, target_sync = published_result
    hub_sync = sync_hub_after_map_publish("default-site-a", published.id)
    return MapPublishResponse(map=published, targetSync=target_sync, hubSync=hub_sync)


@app.post("/api/v1/imports/map", response_model=MapImportResponse)
def import_map(request: MapDraftCreate) -> MapImportResponse:
    issues = store.validate_map(request.map)
    draft_id = store.save_draft(request.map)
    return MapImportResponse(draftId=draft_id, ok=len(issues) == 0, issues=issues, map=request.map)


@app.get("/api/v1/robots", response_model=list[RobotState])
def list_robots() -> list[RobotState]:
    return store.robots()


@app.post("/api/v1/robots", response_model=RobotState)
def create_robot(request: RobotCreate) -> RobotState:
    robot = RobotState(
        robotId=request.robotCode,
        robotType=request.robotType,
        state=request.state,
        x=request.x,
        y=request.y,
        progress=0,
        currentAction=request.currentAction,
        updatedAt=utc_now(),
    )
    try:
        created = store.create_robot(robot)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store.append_message(
        MessageRecord(
            messageId=new_id("msg"),
            messageType="system",
            source="platform-api",
            topic=f"api/robots/{created.robotId}/created",
            createdAt=created.updatedAt,
            payload=created.model_dump(),
        )
    )
    return created


@app.post("/api/v1/robots/{robot_id}/state", response_model=RobotState)
def update_robot_state(robot_id: str, robot: RobotState) -> RobotState:
    if robot_id != robot.robotId:
        raise HTTPException(status_code=400, detail="robot id in path and body must match")
    store.upsert_robot_state(robot)
    store.append_message(
        MessageRecord(
            messageId=new_id("msg"),
            messageType="state",
            source="virtual-robot",
            topic=f"api/robots/{robot_id}/state",
            createdAt=utc_now(),
            payload=robot.model_dump(),
        )
    )
    return robot


@app.get("/api/v1/targets", response_model=list[TargetRegistryItem])
def list_targets(targetType: str | None = None, status: str | None = "active") -> list[TargetRegistryItem]:
    if not hasattr(store, "list_targets"):
        raise HTTPException(status_code=501, detail="Target Registry is not available")
    return store.list_targets(target_type=targetType, status=status)


@app.post("/api/v1/targets", response_model=TargetRegistryItem)
def create_target(request: TargetRegistryItemCreate) -> TargetRegistryItem:
    if not hasattr(store, "create_target"):
        raise HTTPException(status_code=501, detail="Target Registry is not available")
    try:
        return store.create_target(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/targets/{target_id}", response_model=TargetRegistryItem)
def get_target(target_id: str) -> TargetRegistryItem:
    target = store.get_target(target_id) if hasattr(store, "get_target") else None
    if target is None:
        raise HTTPException(status_code=404, detail="target not found")
    return target


@app.patch("/api/v1/targets/{target_id}", response_model=TargetRegistryItem)
def update_target(target_id: str, request: TargetRegistryItemUpdate) -> TargetRegistryItem:
    target = store.update_target(target_id, request) if hasattr(store, "update_target") else None
    if target is None:
        raise HTTPException(status_code=404, detail="target not found")
    return target


@app.delete("/api/v1/targets/{target_id}", response_model=TargetRegistryItem)
def delete_target(target_id: str) -> TargetRegistryItem:
    target = store.delete_target(target_id) if hasattr(store, "delete_target") else None
    if target is None:
        raise HTTPException(status_code=404, detail="target not found")
    return target


@app.get("/api/v1/robot-configs", response_model=list[RobotConfig])
def list_robot_configs() -> list[RobotConfig]:
    if not hasattr(store, "list_robot_configs"):
        raise HTTPException(status_code=501, detail="robot config management is not available")
    return store.list_robot_configs()


@app.post("/api/v1/robot-configs", response_model=RobotConfig)
def create_robot_config(request: RobotConfigCreate) -> RobotConfig:
    if not hasattr(store, "create_robot_config"):
        raise HTTPException(status_code=501, detail="robot config management is not available")
    try:
        return store.create_robot_config(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/robot-configs/{robot_code}", response_model=RobotConfig)
def get_robot_config(robot_code: str) -> RobotConfig:
    config = store.get_robot_config(robot_code) if hasattr(store, "get_robot_config") else None
    if config is None:
        raise HTTPException(status_code=404, detail="robot config not found")
    return config


@app.patch("/api/v1/robot-configs/{robot_code}", response_model=RobotConfig)
def update_robot_config(robot_code: str, request: RobotConfigUpdate) -> RobotConfig:
    config = store.update_robot_config(robot_code, request) if hasattr(store, "update_robot_config") else None
    if config is None:
        raise HTTPException(status_code=404, detail="robot config not found")
    return config


@app.post("/api/v1/robot-configs/{robot_code}/enable", response_model=RobotConfig)
def enable_robot_config(robot_code: str) -> RobotConfig:
    config = store.update_robot_config(robot_code, RobotConfigUpdate(status="enabled", enabled=True)) if hasattr(store, "update_robot_config") else None
    if config is None:
        raise HTTPException(status_code=404, detail="robot config not found")
    return config


@app.post("/api/v1/robot-configs/{robot_code}/disable", response_model=RobotConfig)
def disable_robot_config(robot_code: str) -> RobotConfig:
    config = store.update_robot_config(robot_code, RobotConfigUpdate(status="disabled", enabled=False)) if hasattr(store, "update_robot_config") else None
    if config is None:
        raise HTTPException(status_code=404, detail="robot config not found")
    return config


@app.delete("/api/v1/robot-configs/{robot_code}", response_model=RobotConfig)
def delete_robot_config(robot_code: str) -> RobotConfig:
    config = store.delete_robot_config(robot_code) if hasattr(store, "delete_robot_config") else None
    if config is None:
        raise HTTPException(status_code=404, detail="robot config not found")
    return config


@app.get("/api/v1/executors", response_model=list[ExecutorInstance])
def list_executors(robotCode: str | None = None) -> list[ExecutorInstance]:
    if not hasattr(store, "list_executors"):
        raise HTTPException(status_code=501, detail="executor management is not available")
    return store.list_executors(robot_code=robotCode)


@app.post("/api/v1/executors", response_model=ExecutorInstance)
def create_executor(request: ExecutorInstanceCreate) -> ExecutorInstance:
    if not hasattr(store, "create_executor"):
        raise HTTPException(status_code=501, detail="executor management is not available")
    try:
        return store.create_executor(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/executors/{executor_id}", response_model=ExecutorInstance)
def get_executor(executor_id: str) -> ExecutorInstance:
    executor = store.get_executor(executor_id) if hasattr(store, "get_executor") else None
    if executor is None:
        raise HTTPException(status_code=404, detail="executor not found")
    return executor


@app.post("/api/v1/executors/{executor_id}/stop", response_model=ExecutorTransitionResponse)
def stop_executor(executor_id: str) -> ExecutorTransitionResponse:
    executor = store.transition_executor(executor_id, "stopped") if hasattr(store, "transition_executor") else None
    if executor is None:
        raise HTTPException(status_code=404, detail="executor not found")
    return ExecutorTransitionResponse(executor=executor, message="executor marked as stopped")


@app.post("/api/v1/executors/{executor_id}/restart", response_model=ExecutorTransitionResponse)
def restart_executor(executor_id: str) -> ExecutorTransitionResponse:
    executor = store.transition_executor(executor_id, "active") if hasattr(store, "transition_executor") else None
    if executor is None:
        raise HTTPException(status_code=404, detail="executor not found")
    return ExecutorTransitionResponse(executor=executor, message="executor marked as active")


@app.post("/api/v1/executors/{executor_id}/replace", response_model=ExecutorTransitionResponse)
def replace_executor(executor_id: str) -> ExecutorTransitionResponse:
    executor = store.transition_executor(executor_id, "replaced") if hasattr(store, "transition_executor") else None
    if executor is None:
        raise HTTPException(status_code=404, detail="executor not found")
    return ExecutorTransitionResponse(executor=executor, message="executor marked as replaced")


@app.get("/api/v1/executors/{executor_id}/logs", response_model=ExecutorLogResponse)
def get_executor_logs(executor_id: str, limit: int = 100) -> ExecutorLogResponse:
    executor = store.get_executor(executor_id) if hasattr(store, "get_executor") else None
    if executor is None:
        raise HTTPException(status_code=404, detail="executor not found")
    logs = store.executor_logs(executor_id, limit=limit) if hasattr(store, "executor_logs") else []
    return ExecutorLogResponse(executorId=executor.executorId, robotCode=executor.robotCode, logs=logs)


@app.get("/api/v1/messages", response_model=list[MessageRecord])
def list_messages(
    limit: int = 100,
    messageType: str | None = None,
    robotCode: str | None = None,
    commandId: str | None = None,
    traceId: str | None = None,
    taskId: str | None = None,
    requestId: str | None = None,
    event: str | None = None,
    topic: str | None = None,
    source: str | None = None,
    createdFrom: str | None = None,
    createdTo: str | None = None,
) -> list[MessageRecord]:
    return store.query_messages(
        limit=limit,
        message_type=messageType,
        robot_code=robotCode,
        command_id=commandId,
        trace_id=traceId,
        task_id=taskId,
        request_id=requestId,
        event=event,
        topic=topic,
        source=source,
        created_from=createdFrom,
        created_to=createdTo,
    )


@app.post("/api/v1/messages", response_model=MessageRecord)
def create_message(message: MessageRecord) -> MessageRecord:
    store.append_message(message)
    if hasattr(store, "ingest_observation_from_message"):
        store.ingest_observation_from_message(message)
    return message


def _normalize_command_name(command_name: str | None) -> str:
    command_mapping = {command: command for command in action_command_names()}
    command_mapping.update({"move": "goto_pose", "carry": "goto_pose"})
    normalized = command_mapping.get(command_name or "")
    if not normalized:
        raise HTTPException(status_code=400, detail=f"supported commands are {', '.join(action_command_names())}")
    return normalized


def _command_params(command: CommandCreate) -> dict:
    params = {**command.parameters, **command.target, **command.params}
    if "positionCode" not in params and "position_code" in params:
        params["positionCode"] = params.pop("position_code")
    return params


def _command_source(source: str) -> str:
    allowed_sources = {"api", "scheduler", "agent", "system"}
    return source if source in allowed_sources else "api"


def _issue_command(command: CommandCreate) -> CommandResponse:
    robot_code = command.robotCode or command.robotId
    if not robot_code:
        raise HTTPException(status_code=400, detail="robotCode or robotId is required")
    command_name = _normalize_command_name(command.command or command.commandType)
    try:
        if hasattr(store, "resolve_action_params"):
            params = store.resolve_action_params(command_name, _command_params(command))
        else:
            params = validate_action_params(command_name, _command_params(command))
        if hasattr(store, "validate_robot_path_group"):
            store.validate_robot_path_group(robot_code, params.get("pathGroupId"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    command_id = protocol_id("CMD")
    task_id = command.taskId
    request_id = command.requestId
    if command_name == "where":
        task_id = None
        request_id = request_id or protocol_id("REQ")
    else:
        task_id = task_id or protocol_id("TASK")
        request_id = None

    trace_id = command.traceId or protocol_id("TRACE")
    topic = f"factory/dogs/{robot_code}/command"
    payload = {
        "schemaVersion": "1.0",
        "messageType": "command",
        "commandId": command_id,
        "taskId": task_id,
        "requestId": request_id,
        "robotCode": robot_code,
        "traceId": trace_id,
        "command": command_name,
        "issuedAt": utc_now(),
        "timeoutMs": command.timeoutMs,
        "operatorId": command.operatorId or command.issuedBy,
        "source": _command_source(command.issuedBy),
        "params": params,
    }
    store.append_message(
        MessageRecord(
            messageId=command_id,
            messageType="command",
            source=payload["source"],
            topic=topic,
            createdAt=payload["issuedAt"],
            payload=payload,
        )
    )
    published = bridge.publish_command(topic, payload)
    response_payload = {**payload, "mqttPublished": published}
    return CommandResponse(commandId=command_id, topic=topic, payload=response_payload)


@app.post("/api/v1/commands", response_model=CommandResponse)
def create_command(command: CommandCreate) -> CommandResponse:
    return _issue_command(command)


@app.get("/api/v1/action-command-specs", response_model=list[ActionCommandSpec])
def list_action_command_specs() -> list[ActionCommandSpec]:
    return [
        ActionCommandSpec(command=command, **spec)
        for command, spec in ACTION_COMMAND_SPECS.items()
    ]


@app.get("/api/v1/commands/{command_id}/trace")
def get_command_trace(command_id: str) -> dict:
    trace = store.command_trace(command_id)
    return {
        "commandId": command_id,
        "messageCount": len(trace),
        "messages": [message.model_dump() for message in trace],
    }


@app.post("/api/v1/events", response_model=ConsoleEventResponse)
def create_console_event(event: ConsoleEventCreate) -> ConsoleEventResponse:
    event_id = new_id("event")
    topic = f"sim/{bridge.env}/{bridge.site_id}/broadcast/event"
    payload = {
        "messageId": new_id("msg"),
        "messageType": "event",
        "schemaVersion": "1.0",
        "timestamp": utc_now(),
        "env": "dev",
        "siteId": "site-a",
        "sessionId": "session-local",
        "correlationId": event_id,
        "source": "console",
        "payload": {
            "eventType": event.eventType,
            "severity": event.severity,
            "eventData": event.eventData,
            "recoverable": event.severity != "critical",
        },
    }
    store.append_message(
        MessageRecord(
            messageId=payload["messageId"],
            messageType="event",
            source="console",
            topic=topic,
            createdAt=payload["timestamp"],
            payload=payload,
        )
    )
    published = bridge.publish(topic, payload, qos=1, retain=False)
    return ConsoleEventResponse(eventId=event_id, topic=topic, payload=payload, mqttPublished=published)


def _broadcast_runtime_event(
    run_id: str,
    event_id: str,
    event_type: str,
    target_type: str,
    target_id: str | None,
    severity: str,
    task_id: str | None,
    trace_id: str | None,
    data: dict,
) -> bool:
    topic = f"sim/{bridge.env}/{bridge.site_id}/broadcast/event"
    timestamp = utc_now()
    robot_code = target_id if target_type == "robot" else None
    payload = {
        "messageId": new_id("msg"),
        "messageType": "event",
        "schemaVersion": "1.0",
        "timestamp": timestamp,
        "env": bridge.env,
        "siteId": bridge.site_id,
        "runId": run_id,
        "correlationId": event_id,
        "source": "simulation-console",
        "commandId": None,
        "taskId": task_id,
        "requestId": None,
        "robotCode": robot_code,
        "traceId": trace_id or run_id,
        "event": event_type,
        "payload": {
            "eventType": event_type,
            "targetType": target_type,
            "targetId": target_id,
            "severity": severity,
            "eventData": data,
            "recoverable": severity != "critical",
        },
    }
    store.append_message(
        MessageRecord(
            messageId=payload["messageId"],
            messageType="event",
            source="simulation-console",
            topic=topic,
            createdAt=timestamp,
            payload=payload,
        )
    )
    return bridge.publish(topic, payload, qos=1, retain=False)


def _record_agent_decision(decision: AgentDecision) -> None:
    store.append_message(
        MessageRecord(
            messageId=decision.decisionId,
            messageType="agentDecision",
            source=decision.agentId,
            topic=f"agent/decisions/{decision.decisionId}",
            createdAt=decision.createdAt,
            payload=decision.model_dump(),
        )
    )


def _enabled_robot_codes() -> set[str]:
    if not hasattr(store, "list_robot_configs"):
        return {robot.robotId for robot in store.robots()}
    return {
        config.robotCode
        for config in store.list_robot_configs()
        if config.enabled and config.status == "enabled"
    }


def _select_robot_for_schedule(request: RuleScheduleRequest, state: CurrentState | None) -> str | None:
    enabled = _enabled_robot_codes()
    robots = []
    if state:
        robots = [
            item
            for item in state.robotStates
            if str(item.get("robotId") or item.get("robotCode")) in enabled
            and str(item.get("state", "")).lower() not in {"offline", "disabled", "error"}
        ]
    if not robots:
        robots = [robot.model_dump() for robot in store.robots() if robot.robotId in enabled]
    if request.robotCode:
        return request.robotCode if request.robotCode in {str(item.get("robotId") or item.get("robotCode")) for item in robots} else None
    if not robots:
        return None
    if request.strategy == "lowest_load":
        return str(min(robots, key=lambda item: int(item.get("progress") or 0)).get("robotId") or robots[0].get("robotCode"))
    return str(robots[0].get("robotId") or robots[0].get("robotCode"))


@app.post("/api/v1/exports", response_model=ExportResponse)
def create_export(request: ExportCreate) -> ExportResponse:
    export_id, file_name = store.create_export(request.exportType)
    return ExportResponse(
        exportId=export_id,
        fileName=file_name,
        url=f"/api/v1/exports/{file_name}",
    )


@app.get("/api/v1/exports/{file_name}")
def download_export(file_name: str) -> FileResponse:
    file_path = (EXPORT_DIR / file_name).resolve()
    if EXPORT_DIR.resolve() not in file_path.parents or not file_path.exists():
        raise HTTPException(status_code=404, detail="export file not found")
    return FileResponse(path=Path(file_path), media_type="application/json", filename=file_name)


@app.get("/api/v1/mqtt/contract")
def get_mqtt_contract() -> dict:
    return MQTT_CONTRACT


@app.get("/api/v1/scenarios", response_model=list[ScenarioSummary])
def list_scenarios() -> list[ScenarioSummary]:
    return store.list_scenarios()


@app.get("/api/v1/scenarios/{scenario_id}", response_model=ScenarioSummary)
def get_scenario(scenario_id: str) -> ScenarioSummary:
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return scenario


@app.get("/api/v1/scenarios/{scenario_id}/validation", response_model=ScenarioValidationResponse)
def validate_scenario(scenario_id: str) -> ScenarioValidationResponse:
    if not hasattr(store, "validate_scenario"):
        raise HTTPException(status_code=501, detail="scenario validation requires database store")
    validation = store.validate_scenario(scenario_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return validation


@app.get("/api/v1/task-templates", response_model=list[TaskTemplate])
def list_task_templates() -> list[TaskTemplate]:
    return store.list_task_templates()


@app.post("/api/v1/simulation-runs", response_model=SimulationRun)
def create_simulation_run(request: SimulationRunCreate) -> SimulationRun:
    try:
        return store.create_simulation_run(request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/simulation-runs", response_model=list[SimulationRun])
def list_simulation_runs(limit: int = 20) -> list[SimulationRun]:
    return store.list_simulation_runs(limit=limit)


@app.get("/api/v1/simulation-runs/{run_id}", response_model=SimulationRun)
def get_simulation_run(run_id: str) -> SimulationRun:
    run = store.get_simulation_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return run


@app.post("/api/v1/simulation-runs/{run_id}/start", response_model=SimulationRun)
def start_simulation_run(run_id: str) -> SimulationRun:
    run = store.update_simulation_run_status(run_id, "Running")
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return run


@app.post("/api/v1/simulation-runs/{run_id}/pause", response_model=SimulationRun)
def pause_simulation_run(run_id: str) -> SimulationRun:
    run = store.update_simulation_run_status(run_id, "Paused")
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return run


@app.post("/api/v1/simulation-runs/{run_id}/resume", response_model=SimulationRun)
def resume_simulation_run(run_id: str) -> SimulationRun:
    run = store.update_simulation_run_status(run_id, "Running")
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return run


@app.post("/api/v1/simulation-runs/{run_id}/stop", response_model=SimulationRun)
def stop_simulation_run(run_id: str) -> SimulationRun:
    run = store.update_simulation_run_status(run_id, "Stopped")
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return run


@app.post("/api/v1/simulation-runs/{run_id}/tasks", response_model=SimulationTask)
def create_simulation_task(run_id: str, request: SimulationTaskCreate) -> SimulationTask:
    task = store.create_simulation_task(run_id, request)
    if task is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return task


@app.post("/api/v1/simulation-runs/{run_id}/tasks/from-template", response_model=SimulationTask)
def create_simulation_task_from_template(run_id: str, request: TaskFromTemplateCreate) -> SimulationTask:
    task = store.create_task_from_template(run_id, request)
    if task is None:
        raise HTTPException(status_code=404, detail="simulation run or task template not found")
    return task


@app.post("/api/v1/simulation-runs/{run_id}/tasks/batch", response_model=BatchTaskResponse)
def create_simulation_tasks_batch(run_id: str, request: BatchTaskCreate) -> BatchTaskResponse:
    if not hasattr(store, "create_batch_tasks"):
        raise HTTPException(status_code=501, detail="batch task creation requires database store")
    response = store.create_batch_tasks(run_id, request)
    if response is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return response


@app.post("/api/v1/simulation-runs/{run_id}/task-chains", response_model=TaskChain)
def create_simulation_task_chain(run_id: str, request: TaskChainCreate) -> TaskChain:
    if not hasattr(store, "create_task_chain"):
        raise HTTPException(status_code=501, detail="task chain creation requires database store")
    chain = store.create_task_chain(run_id, request)
    if chain is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return chain


@app.get("/api/v1/simulation-runs/{run_id}/task-chains", response_model=list[TaskChain])
def list_simulation_task_chains(run_id: str) -> list[TaskChain]:
    if not hasattr(store, "list_task_chains"):
        raise HTTPException(status_code=501, detail="task chain listing requires database store")
    return store.list_task_chains(run_id)


@app.get("/api/v1/task-chains/{chain_id}", response_model=TaskChain)
def get_simulation_task_chain(chain_id: str) -> TaskChain:
    if not hasattr(store, "get_task_chain"):
        raise HTTPException(status_code=501, detail="task chain lookup requires database store")
    chain = store.get_task_chain(chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="task chain not found")
    return chain


@app.get("/api/v1/simulation-runs/{run_id}/tasks", response_model=list[SimulationTask])
def list_simulation_run_tasks(run_id: str) -> list[SimulationTask]:
    return store.list_run_tasks(run_id)


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task_to_hub_response(task)


@app.get("/api/v1/tasks/{task_id}/plans")
def list_task_plans(task_id: str) -> list[dict]:
    return [plan.model_dump() for plan in store.list_task_plans(task_id)]


@app.post("/api/v1/tasks/{task_id}/plans", response_model=SimulationPlan)
def create_task_plan(task_id: str, request: SimulationPlanCreate) -> SimulationPlan:
    if not hasattr(store, "create_task_plan"):
        raise HTTPException(status_code=501, detail="manual plan creation requires database store")
    plan = store.create_task_plan(task_id, request)
    if plan is None:
        raise HTTPException(status_code=404, detail="task not found")
    return plan


@app.post("/api/v1/tasks/{task_id}/replan", response_model=SimulationPlan)
def replan_task(task_id: str, request: SimulationPlanCreate) -> SimulationPlan:
    return create_task_plan(task_id, request)


@app.get("/api/v1/tasks/{task_id}/trace", response_model=TraceResponse)
def get_task_trace(task_id: str) -> TraceResponse:
    trace = store.get_task_trace(task_id)
    if trace.status == "NotFound":
        raise HTTPException(status_code=404, detail="task trace not found")
    return trace


@app.post("/api/v1/actions")
def create_action(request: ActionCreate | HubActionCreate = Body(...)) -> dict[str, Any] | SimulationAction:
    if isinstance(request, HubActionCreate):
        return create_hub_action(store, request.model_dump(), _issue_command)
    action_request = request
    if action_request.robotCode and hasattr(store, "validate_robot_path_group"):
        try:
            store.validate_robot_path_group(action_request.robotCode, action_request.params.get("pathGroupId"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        action = store.create_action(action_request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if action is None:
        raise HTTPException(status_code=404, detail="simulation run or task not found")
    command_response = _issue_command(
        CommandCreate(
            robotCode=action.robotCode,
            command=action.command,
            params=action.params,
            timeoutMs=action.timeoutMs,
            issuedBy="agent",
            operatorId=action_request.operatorId,
            taskId=action.taskId,
            traceId=action.traceId,
        )
    )
    issued = store.mark_action_issued(
        action.actionId,
        command_response.commandId,
        command_response.payload.get("requestId"),
        command_response.payload,
    )
    if issued is None:
        raise HTTPException(status_code=500, detail="action issue state lost")
    return issued


@app.get("/api/v1/actions")
def list_actions(runId: str | None = None, taskId: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return [action_to_hub_response(action) for action in store.list_actions(run_id=runId, task_id=taskId, limit=limit)]


@app.get("/api/v1/actions/{action_id}")
def get_action(action_id: str) -> dict[str, Any]:
    action = store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    return action_to_hub_response(action)


@app.get("/api/v1/actions/{action_id}/trace", response_model=TraceResponse)
def get_action_trace(action_id: str) -> TraceResponse:
    trace = store.get_action_trace(action_id)
    if trace.status == "NotFound":
        raise HTTPException(status_code=404, detail="action trace not found")
    return trace


@app.post("/api/v1/actions/{action_id}/stop", response_model=SimulationAction)
def stop_action(action_id: str) -> SimulationAction:
    action = store.stop_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    return action


@app.get("/api/v1/current-states/{run_id}", response_model=CurrentState)
def get_current_state(run_id: str) -> CurrentState:
    state = store.get_current_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="current state not found")
    return state


@app.post("/api/v1/simulation-runs/{run_id}/schedule", response_model=RuleScheduleResponse)
def schedule_simulation_run(run_id: str, request: RuleScheduleRequest) -> RuleScheduleResponse:
    run = store.get_simulation_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    tasks = store.list_run_tasks(run_id)
    task = next((item for item in tasks if item.taskId == request.taskId), None) if request.taskId else (tasks[0] if tasks else None)
    state = store.get_current_state(run_id)
    trace_id = task.traceId if task else run_id
    selected_robot = _select_robot_for_schedule(request, state)

    if task is None or task.activePlan is None:
        decision = AgentDecision(
            decisionId=protocol_id("DECISION"),
            runId=run_id,
            taskId=task.taskId if task else None,
            traceId=trace_id,
            decisionType="failed",
            inputRefs={"taskId": request.taskId, "strategy": request.strategy},
            currentStateVersion=state.stateVersion if state else None,
            selectedRobotCode=selected_robot,
            reason="no task or active plan available for scheduling",
            confidence=1.0,
            createdAt=utc_now(),
        )
        _record_agent_decision(decision)
        return RuleScheduleResponse(decision=decision, action=None, currentState=state)

    if selected_robot is None:
        decision = AgentDecision(
            decisionId=protocol_id("DECISION"),
            runId=run_id,
            taskId=task.taskId,
            traceId=trace_id,
            decisionType="wait",
            inputRefs={"taskId": task.taskId, "strategy": request.strategy},
            currentStateVersion=state.stateVersion if state else None,
            selectedRobotCode=None,
            planId=task.activePlan.planId,
            reason="no enabled online robot is available",
            confidence=1.0,
            createdAt=utc_now(),
        )
        _record_agent_decision(decision)
        return RuleScheduleResponse(decision=decision, action=None, currentState=state)

    existing_plan_step_ids = {
        action.planStepId
        for action in store.list_actions(run_id=run_id, task_id=task.taskId, limit=1000)
        if action.planId == task.activePlan.planId and action.planStepId
    }
    step = next(
        (
            item
            for item in task.activePlan.steps
            if item.status in {"Pending", "Ready"} and item.planStepId not in existing_plan_step_ids
        ),
        None,
    )
    if step is None:
        decision = AgentDecision(
            decisionId=protocol_id("DECISION"),
            runId=run_id,
            taskId=task.taskId,
            traceId=trace_id,
            decisionType="wait",
            inputRefs={"taskId": task.taskId, "planId": task.activePlan.planId, "strategy": request.strategy},
            currentStateVersion=state.stateVersion if state else None,
            selectedRobotCode=selected_robot,
            planId=task.activePlan.planId,
            reason="no pending plan step is available for scheduling",
            confidence=1.0,
            createdAt=utc_now(),
        )
        _record_agent_decision(decision)
        return RuleScheduleResponse(decision=decision, action=None, currentState=state)

    action: SimulationAction | None = None
    action_ids: list[str] = []
    decision_type = "plan_created"
    reason = f"rule scheduler selected {selected_robot} by {request.strategy}"
    if request.autoIssue:
        try:
            if hasattr(store, "validate_robot_path_group"):
                step_params = step.params or step.target
                store.validate_robot_path_group(selected_robot, step_params.get("pathGroupId"))
            created = store.create_action(
                ActionCreate(
                    runId=run_id,
                    taskId=task.taskId,
                    planId=task.activePlan.planId,
                    planStepId=step.planStepId,
                    robotCode=selected_robot,
                    command=step.actionType,
                    params=step.params or step.target,
                    timeoutMs=step.timeoutMs,
                    operatorId=request.operatorId,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if created is None:
            raise HTTPException(status_code=404, detail="simulation task not found")
        command_response = _issue_command(
            CommandCreate(
                robotCode=created.robotCode,
                command=created.command,
                params=created.params,
                timeoutMs=created.timeoutMs,
                issuedBy="agent",
                operatorId=request.operatorId,
                taskId=created.taskId,
                traceId=created.traceId,
            )
        )
        action = store.mark_action_issued(
            created.actionId,
            command_response.commandId,
            command_response.payload.get("requestId"),
            command_response.payload,
        )
        action_ids = [created.actionId]
        decision_type = "action_created"
        reason = f"rule scheduler selected {selected_robot} and issued {created.command}"

    decision = AgentDecision(
        decisionId=protocol_id("DECISION"),
        runId=run_id,
        taskId=task.taskId,
        traceId=trace_id,
        decisionType=decision_type,
        inputRefs={"taskId": task.taskId, "planId": task.activePlan.planId, "planStepId": step.planStepId, "strategy": request.strategy},
        currentStateVersion=state.stateVersion if state else None,
        selectedRobotCode=selected_robot,
        planId=task.activePlan.planId,
        actionIds=action_ids,
        reason=reason,
        confidence=1.0,
        createdAt=utc_now(),
    )
    _record_agent_decision(decision)
    return RuleScheduleResponse(decision=decision, action=action, currentState=store.get_current_state(run_id))


@app.get("/api/v1/simulation-runs/{run_id}/robots", response_model=list[RobotState])
def list_simulation_run_robots(run_id: str) -> list[RobotState]:
    if store.get_simulation_run(run_id) is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return store.robots()


@app.get("/api/v1/simulation-runs/{run_id}/resources")
def list_simulation_run_resources(run_id: str) -> dict:
    state = store.get_current_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="current state not found")
    return state.resourceStates


@app.get("/api/v1/simulation-runs/{run_id}/messages", response_model=list[MessageRecord])
def list_simulation_run_messages(
    run_id: str,
    limit: int = 100,
    category: str | None = None,
) -> list[MessageRecord]:
    return store.list_run_messages(run_id, limit=limit, category=category)


@app.get("/api/v1/simulation-runs/{run_id}/message-metrics")
def get_simulation_run_message_metrics(run_id: str) -> dict:
    if store.get_simulation_run(run_id) is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return store.run_message_metrics(run_id)


@app.post("/api/v1/simulation-runs/{run_id}/messages/{message_id}/replay", response_model=MessageReplayResponse)
def replay_simulation_run_message(
    run_id: str,
    message_id: str,
    request: MessageReplayCreate,
) -> MessageReplayResponse:
    if not hasattr(store, "replay_run_message"):
        raise HTTPException(status_code=501, detail="message replay requires database store")
    response = store.replay_run_message(run_id, message_id, request)
    if response is None:
        raise HTTPException(status_code=404, detail="message not found in simulation run")
    return response


@app.get("/api/v1/simulation-runs/{run_id}/observations", response_model=list[Observation])
def list_simulation_run_observations(run_id: str, limit: int = 100) -> list[Observation]:
    return store.list_observations(run_id, limit=limit)


@app.post("/api/v1/simulation-runs/{run_id}/events", response_model=Observation)
def inject_simulation_event(run_id: str, request: SimulationEventCreate) -> Observation:
    observation = store.inject_simulation_event(run_id, request)
    if observation is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    _broadcast_runtime_event(
        run_id=run_id,
        event_id=observation.eventId or observation.observationId,
        event_type=request.eventType,
        target_type=request.targetType,
        target_id=request.targetId,
        severity=request.severity,
        task_id=observation.taskId,
        trace_id=observation.traceId,
        data={
            "durationMs": request.durationMs,
            "autoRecover": request.autoRecover,
            **request.data,
        },
    )
    return observation


@app.post("/api/v1/simulation-runs/{run_id}/events/recover", response_model=Observation)
def recover_simulation_event(run_id: str, request: SimulationEventRecoveryCreate) -> Observation:
    if not hasattr(store, "recover_simulation_event"):
        raise HTTPException(status_code=501, detail="event recovery requires database store")
    observation = store.recover_simulation_event(run_id, request)
    if observation is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    _broadcast_runtime_event(
        run_id=run_id,
        event_id=observation.eventId or observation.observationId,
        event_type="fault.recovered",
        target_type=request.targetType,
        target_id=request.targetId,
        severity="info",
        task_id=observation.taskId,
        trace_id=observation.traceId,
        data={
            "eventType": request.eventType,
            "recoveryMode": request.recoveryMode,
            "reason": request.reason,
            "operatorId": request.operatorId,
        },
    )
    return observation


@app.post("/api/v1/simulation-runs/{run_id}/snapshots", response_model=Snapshot)
def create_simulation_snapshot(run_id: str, request: SnapshotCreate) -> Snapshot:
    snapshot = store.create_snapshot(run_id, request)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="current state not found")
    return snapshot


@app.get("/api/v1/simulation-runs/{run_id}/snapshots", response_model=list[Snapshot])
def list_simulation_snapshots(run_id: str) -> list[Snapshot]:
    return store.list_snapshots(run_id)


@app.get("/api/v1/traces/{trace_id}")
def get_trace(trace_id: str) -> dict[str, Any] | TraceResponse:
    trace = store.get_trace(trace_id)
    if trace.status == "NotFound":
        hub_trace = find_hub_object(store, "trace", trace_id)
        if hub_trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return {
            **hub_trace,
            "traceId": hub_trace.get("trace_id") or trace_id,
            "status": "Open",
            "spans": [],
        }
    return trace


@app.get("/api/v1/traces/{trace_id}/spans", response_model=list)
def get_trace_spans(trace_id: str) -> list:
    trace = get_trace(trace_id)
    if isinstance(trace, dict):
        return trace.get("spans", [])
    return [span.model_dump() for span in trace.spans]


@app.get("/api/v1/traces/{trace_id}/graph")
def get_trace_graph(trace_id: str) -> dict:
    graph = store.get_trace_graph(trace_id)
    if graph.get("status") == "NotFound":
        raise HTTPException(status_code=404, detail="trace not found")
    return graph


@app.get("/api/v1/simulation-runs/{run_id}/export")
def export_simulation_run(run_id: str) -> dict:
    payload = store.export_simulation_run(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return payload


@app.websocket("/ws/v1/workspaces/{workspace_id}/runs/{run_id}")
async def simulation_run_socket(websocket: WebSocket, workspace_id: str, run_id: str) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(
                {
                    "messageId": new_id("ws"),
                    "type": "simulation.snapshot",
                    "workspaceId": workspace_id,
                    "runId": run_id,
                    "timestamp": utc_now(),
                    "data": {
                        "run": store.get_simulation_run(run_id).model_dump() if store.get_simulation_run(run_id) else None,
                        "currentState": store.get_current_state(run_id).model_dump() if store.get_current_state(run_id) else None,
                        "tasks": [task.model_dump() for task in store.list_run_tasks(run_id)],
                        "actions": [action.model_dump() for action in store.list_actions(run_id=run_id, limit=20)],
                        "messages": [message.model_dump() for message in store.list_run_messages(run_id, limit=20)],
                        "observations": [item.model_dump() for item in store.list_observations(run_id, limit=20)],
                    },
                }
            )
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


register_hub_compat_routes(app, lambda: store)


@app.websocket("/ws/v1/sessions/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(
                {
                    "messageId": new_id("ws"),
                    "type": "snapshot",
                    "sessionId": session_id,
                    "timestamp": utc_now(),
                    "data": {
                        "robots": [robot.model_dump() for robot in store.runtime_robots()],
                        "messages": [message.model_dump() for message in store.runtime_messages(limit=20)],
                    },
                }
            )
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
