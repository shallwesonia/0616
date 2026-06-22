from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .mqtt_bridge import PlatformMqttBridge
from .mqtt_contract import MQTT_CONTRACT
from .schemas import (
    CommandCreate,
    CommandResponse,
    ConsoleEventCreate,
    ConsoleEventResponse,
    DraftResponse,
    ExportCreate,
    ExportResponse,
    MapImportResponse,
    MapDraftCreate,
    MessageRecord,
    RobotState,
    SiteMap,
    ValidationResponse,
    new_id,
    protocol_id,
    utc_now,
)
from .store import EXPORT_DIR
from .store_factory import create_store


store = create_store()
bridge = PlatformMqttBridge(store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    bridge.start()
    yield
    bridge.stop()


app = FastAPI(
    title="Embodied Workflow Simulation Platform API",
    version="0.1.0",
    description="2D environment configuration, message hub facade, export and robot state API.",
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
                "supportedCommands": ["goto_pose", "stop", "where"],
                "resultEvents": [
                    "command.accepted",
                    "command.rejected",
                    "task.started",
                    "task.succeeded",
                    "task.failed",
                    "task.stopped",
                    "task.timeout",
                    "pose.updated",
                    "where.result",
                    "where.failed",
                    "device.offline",
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


@app.post("/api/v1/maps/{map_id}/drafts/{draft_id}/publish", response_model=SiteMap)
def publish_map_draft(map_id: str, draft_id: str) -> SiteMap:
    draft = store.draft_map(draft_id)
    if draft is None or draft.id != map_id:
        raise HTTPException(status_code=404, detail="draft not found")
    issues = store.validate_map(draft)
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})
    published = store.publish_draft(draft_id)
    if published is None:
        raise HTTPException(status_code=404, detail="draft not found")
    return published


@app.post("/api/v1/imports/map", response_model=MapImportResponse)
def import_map(request: MapDraftCreate) -> MapImportResponse:
    issues = store.validate_map(request.map)
    draft_id = store.save_draft(request.map)
    return MapImportResponse(draftId=draft_id, ok=len(issues) == 0, issues=issues, map=request.map)


@app.get("/api/v1/robots", response_model=list[RobotState])
def list_robots() -> list[RobotState]:
    return store.robots()


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
    return message


def _normalize_command_name(command_name: str | None) -> str:
    command_mapping = {
        "move": "goto_pose",
        "carry": "goto_pose",
        "goto_pose": "goto_pose",
        "stop": "stop",
        "where": "where",
    }
    normalized = command_mapping.get(command_name or "")
    if not normalized:
        raise HTTPException(status_code=400, detail="supported commands are goto_pose, stop and where")
    return normalized


def _command_params(command: CommandCreate) -> dict:
    params = {**command.parameters, **command.target, **command.params}
    if "positionCode" not in params and "position_code" in params:
        params["positionCode"] = params.pop("position_code")
    return params


def _command_source(source: str) -> str:
    allowed_sources = {"api", "scheduler", "agent", "system"}
    return source if source in allowed_sources else "api"


@app.post("/api/v1/commands", response_model=CommandResponse)
def create_command(command: CommandCreate) -> CommandResponse:
    robot_code = command.robotCode or command.robotId
    if not robot_code:
        raise HTTPException(status_code=400, detail="robotCode or robotId is required")
    command_name = _normalize_command_name(command.command or command.commandType)
    params = _command_params(command)
    if command_name == "goto_pose" and not {"x", "y"}.issubset(params):
        raise HTTPException(status_code=400, detail="goto_pose requires params.x and params.y")

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
    topic = "sim/dev/site-a/broadcast/event"
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
