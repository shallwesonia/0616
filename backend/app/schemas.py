from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


MapObjectType = Literal["zone", "obstacle", "station", "pathNode", "resourcePoint"]
ActionCommand = Literal[
    "goto_pose",
    "where",
    "stop",
    "pick",
    "place",
    "load",
    "unload",
    "inspect",
    "charge",
    "wait",
]

ACTION_COMMAND_SPECS: dict[str, dict[str, Any]] = {
    "goto_pose": {
        "label": "Move to pose",
        "required": ["x", "y"],
        "defaults": {"z": 0, "yaw": 0, "speed": 1.0, "tolerance": 50},
        "fields": {
            "x": {"type": "number", "required": True, "label": "目标 X"},
            "y": {"type": "number", "required": True, "label": "目标 Y"},
            "z": {"type": "number", "required": False, "label": "目标 Z"},
            "yaw": {"type": "number", "required": False, "label": "Yaw"},
            "speed": {"type": "number", "required": False, "label": "速度"},
            "tolerance": {"type": "number", "required": False, "label": "容差"},
        },
    },
    "where": {
        "label": "Query robot state",
        "required": [],
        "defaults": {"queryMode": "pose"},
        "fields": {
            "queryMode": {
                "type": "select",
                "required": False,
                "label": "查询模式",
                "options": ["pose", "state", "full"],
            }
        },
    },
    "stop": {
        "label": "Stop robot action",
        "required": [],
        "defaults": {"stopScope": "current_action", "reason": "manual_stop"},
        "fields": {
            "stopScope": {
                "type": "select",
                "required": False,
                "label": "停止范围",
                "options": ["current_action", "task", "robot"],
            },
            "reason": {"type": "string", "required": False, "label": "停止原因"},
        },
    },
    "pick": {
        "label": "Pick",
        "required": ["targetId"],
        "defaults": {"durationMinMs": 3000, "durationMaxMs": 5000},
        "fields": {
            "targetId": {"type": "string", "required": True, "label": "目标对象"},
            "durationMinMs": {"type": "number", "required": False, "label": "最短耗时 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长耗时 ms"},
        },
    },
    "place": {
        "label": "Place",
        "required": ["targetId"],
        "defaults": {"durationMinMs": 3000, "durationMaxMs": 5000},
        "fields": {
            "targetId": {"type": "string", "required": True, "label": "目标位置"},
            "durationMinMs": {"type": "number", "required": False, "label": "最短耗时 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长耗时 ms"},
        },
    },
    "load": {
        "label": "Load",
        "required": ["stationId"],
        "defaults": {"durationMinMs": 5000, "durationMaxMs": 8000},
        "fields": {
            "stationId": {"type": "string", "required": True, "label": "装载工位"},
            "durationMinMs": {"type": "number", "required": False, "label": "最短耗时 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长耗时 ms"},
        },
    },
    "unload": {
        "label": "Unload",
        "required": ["stationId"],
        "defaults": {"durationMinMs": 5000, "durationMaxMs": 8000},
        "fields": {
            "stationId": {"type": "string", "required": True, "label": "卸载工位"},
            "durationMinMs": {"type": "number", "required": False, "label": "最短耗时 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长耗时 ms"},
        },
    },
    "inspect": {
        "label": "Inspect",
        "required": ["targetId"],
        "defaults": {"durationMinMs": 4000, "durationMaxMs": 7000},
        "fields": {
            "targetId": {"type": "string", "required": True, "label": "巡检对象"},
            "durationMinMs": {"type": "number", "required": False, "label": "最短耗时 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长耗时 ms"},
        },
    },
    "charge": {
        "label": "Charge",
        "required": [],
        "defaults": {"targetBattery": 95, "durationMinMs": 10000, "durationMaxMs": 15000},
        "fields": {
            "stationId": {"type": "string", "required": False, "label": "充电点"},
            "targetBattery": {"type": "number", "required": False, "label": "目标电量"},
            "durationMinMs": {"type": "number", "required": False, "label": "最短耗时 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长耗时 ms"},
        },
    },
    "wait": {
        "label": "Wait",
        "required": [],
        "defaults": {"durationMinMs": 1000, "durationMaxMs": 3000},
        "fields": {
            "durationMinMs": {"type": "number", "required": False, "label": "最短等待 ms"},
            "durationMaxMs": {"type": "number", "required": False, "label": "最长等待 ms"},
            "reason": {"type": "string", "required": False, "label": "等待原因"},
        },
    },
}


def action_command_names() -> list[str]:
    return list(ACTION_COMMAND_SPECS.keys())


def validate_action_params(command: str, params: dict[str, Any] | None) -> dict[str, Any]:
    spec = ACTION_COMMAND_SPECS.get(command)
    if spec is None:
        raise ValueError(f"unsupported command: {command}")
    normalized = {**spec.get("defaults", {}), **(params or {})}
    for field_name in spec.get("required", []):
        if normalized.get(field_name) in {None, ""}:
            raise ValueError(f"{command} requires params.{field_name}")
    for field_name, field_spec in spec.get("fields", {}).items():
        if field_name not in normalized or normalized[field_name] in {None, ""}:
            continue
        if field_spec.get("type") == "number":
            try:
                normalized[field_name] = float(normalized[field_name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{command} params.{field_name} must be a number") from exc
        if field_spec.get("type") == "select" and normalized[field_name] not in set(field_spec.get("options", [])):
            raise ValueError(f"{command} params.{field_name} must be one of {field_spec.get('options', [])}")
    if "durationMinMs" in normalized and "durationMaxMs" in normalized:
        if float(normalized["durationMinMs"]) < 0 or float(normalized["durationMaxMs"]) < float(normalized["durationMinMs"]):
            raise ValueError(f"{command} duration range is invalid")
    if "speed" in normalized and float(normalized["speed"]) <= 0:
        raise ValueError("goto_pose params.speed must be greater than zero")
    if "tolerance" in normalized and float(normalized["tolerance"]) < 0:
        raise ValueError("goto_pose params.tolerance must not be negative")
    return normalized


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def protocol_id(prefix: str) -> str:
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{date_part}-{uuid4().hex[:6].upper()}"


class MapObject(BaseModel):
    id: str
    type: MapObjectType
    name: str
    x: float
    y: float
    width: float | None = None
    height: float | None = None
    radius: float | None = None
    color: str


class PathEdge(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    direction: Literal["one_way", "two_way"] = "two_way"
    capacity: int = 1

    model_config = {"populate_by_name": True}


class SiteMap(BaseModel):
    id: str
    name: str
    width: float
    height: float
    unit: str = "mm"
    gridSize: int = 40
    configVersion: str
    objects: list[MapObject]
    pathEdges: list[PathEdge] = []


class MapDraftCreate(BaseModel):
    map: SiteMap


class DraftResponse(BaseModel):
    draftId: str
    map: SiteMap


class ValidationResponse(BaseModel):
    ok: bool
    issues: list[str]


class RobotState(BaseModel):
    robotId: str
    robotType: str
    state: str
    x: float
    y: float
    progress: int = 0
    currentAction: str
    updatedAt: str


class MessageRecord(BaseModel):
    messageId: str
    messageType: str
    source: str
    topic: str
    createdAt: str
    payload: dict[str, Any] = {}


class ExportCreate(BaseModel):
    exportType: str


class ExportResponse(BaseModel):
    exportId: str
    fileName: str
    url: str


class MapImportResponse(BaseModel):
    draftId: str
    ok: bool
    issues: list[str]
    map: SiteMap


class ConsoleEventCreate(BaseModel):
    eventType: str
    severity: Literal["info", "warning", "error", "critical"] = "warning"
    eventData: dict[str, Any] = {}


class ConsoleEventResponse(BaseModel):
    eventId: str
    topic: str
    payload: dict[str, Any]
    mqttPublished: bool


class CommandCreate(BaseModel):
    robotId: str | None = None
    robotCode: str | None = None
    commandType: str | None = None
    command: str | None = None
    target: dict[str, Any] = {}
    params: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    timeoutMs: int = 60000
    priority: int = 5
    issuedBy: str = "agent"
    operatorId: str | None = None
    taskId: str | None = None
    requestId: str | None = None
    traceId: str | None = None
    idempotencyKey: str | None = None


class CommandResponse(BaseModel):
    commandId: str
    topic: str
    payload: dict[str, Any]


class ActionCommandSpec(BaseModel):
    command: str
    label: str
    required: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)


class ScenarioSummary(BaseModel):
    scenarioId: str
    name: str
    siteMapId: str
    siteMapVersion: str
    robotCodes: list[str]
    robotTypeIds: list[str]
    actionSet: dict[str, Any]
    taskFlow: dict[str, Any]
    resourceProfile: dict[str, Any]
    map: SiteMap


class ScenarioValidationCheck(BaseModel):
    code: str
    label: str
    status: Literal["passed", "warning", "failed"]
    detail: str


class ScenarioValidationResponse(BaseModel):
    scenarioId: str
    ok: bool
    issues: list[str] = Field(default_factory=list)
    checks: list[ScenarioValidationCheck] = Field(default_factory=list)


class SimulationRunCreate(BaseModel):
    scenarioId: str = "default-site-a"
    name: str | None = None


class SimulationRun(BaseModel):
    runId: str
    scenarioId: str
    name: str
    status: str
    mapId: str
    mapVersion: str
    scenario: dict[str, Any]
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    updatedAt: str


class TaskTemplate(BaseModel):
    templateId: str
    name: str
    description: str
    defaultGoal: str
    defaultInput: dict[str, Any] = Field(default_factory=dict)
    supportedCommands: list[str] = Field(default_factory=list)


class SimulationTaskCreate(BaseModel):
    goal: str
    input: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    priority: int = 5
    expectedOutcome: str | None = None
    createdBy: str = "simulation-console"


class TaskFromTemplateCreate(BaseModel):
    templateId: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    createdBy: str = "simulation-console"


class BatchTaskCreate(BaseModel):
    templateId: str | None = None
    goal: str = "Batch simulation task"
    count: int = Field(default=3, ge=1, le=50)
    intervalMs: int = Field(default=0, ge=0)
    priority: int = 5
    targetRange: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    randomSeed: int | None = None
    randomizeRobot: bool = False
    randomizeTaskType: bool = False
    autoRun: bool = False
    createdBy: str = "simulation-console"


class BatchTaskResponse(BaseModel):
    batchId: str
    runId: str
    requestedCount: int
    createdCount: int
    tasks: list["SimulationTask"] = Field(default_factory=list)


class PlanStep(BaseModel):
    planStepId: str
    sequence: int
    actionType: str
    target: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    dependsOn: list[str] = Field(default_factory=list)
    successCondition: str | None = None
    failurePolicy: str = "surface_to_operator"
    timeoutMs: int = 60000
    status: str = "Pending"


class SimulationPlan(BaseModel):
    planId: str
    runId: str
    taskId: str
    traceId: str
    planVersion: int
    strategy: str
    steps: list[PlanStep]
    dependencies: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    generatedBy: str
    generationLatencyMs: int
    status: str
    createdAt: str
    activatedAt: str | None = None


class SimulationTask(BaseModel):
    taskId: str
    runId: str
    traceId: str
    goal: str
    input: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    priority: int
    expectedOutcome: str | None = None
    status: str
    createdBy: str
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    activePlan: SimulationPlan | None = None


class ActionCreate(BaseModel):
    runId: str
    taskId: str | None = None
    planId: str | None = None
    planStepId: str | None = None
    robotCode: str | None = None
    command: ActionCommand
    params: dict[str, Any] = Field(default_factory=dict)
    timeoutMs: int = 60000
    operatorId: str = "simulation-console"


class SimulationAction(BaseModel):
    actionId: str
    runId: str
    taskId: str | None = None
    planId: str | None = None
    planStepId: str | None = None
    traceId: str
    robotCode: str
    command: str
    params: dict[str, Any] = Field(default_factory=dict)
    commandId: str | None = None
    requestId: str | None = None
    attemptNo: int
    timeoutMs: int
    status: str
    result: dict[str, Any] | None = None
    createdAt: str
    issuedAt: str | None = None
    startedAt: str | None = None
    finishedAt: str | None = None


class Observation(BaseModel):
    observationId: str
    runId: str
    taskId: str | None = None
    actionId: str | None = None
    traceId: str | None = None
    source: str
    event: str
    category: str
    eventId: str | None = None
    messageId: str | None = None
    robotCode: str | None = None
    commandId: str | None = None
    requestId: str | None = None
    timestamp: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    processingStatus: str = "Applied"


class CurrentState(BaseModel):
    runId: str
    stateVersion: int
    taskState: dict[str, Any] = Field(default_factory=dict)
    activePlan: dict[str, Any] | None = None
    robotStates: list[dict[str, Any]] = Field(default_factory=list)
    resourceStates: dict[str, Any] = Field(default_factory=dict)
    environmentState: dict[str, Any] = Field(default_factory=dict)
    pendingActions: list[dict[str, Any]] = Field(default_factory=list)
    activeEvents: list[dict[str, Any]] = Field(default_factory=list)
    lastObservationId: str | None = None
    lastObservationAt: str | None = None
    updatedAt: str


class SimulationEventCreate(BaseModel):
    eventType: Literal[
        "robot.offline",
        "action.failed",
        "path.blocked",
        "interface.timeout",
        "message.dropped",
        "resource.locked",
        "station.unavailable",
        "battery.low",
    ]
    targetType: Literal["robot", "path", "station", "interface", "message", "resource"] = "robot"
    targetId: str | None = None
    severity: Literal["info", "warning", "error", "critical"] = "warning"
    data: dict[str, Any] = Field(default_factory=dict)
    durationMs: int | None = None
    autoRecover: bool = False


class SimulationEventRecoveryCreate(BaseModel):
    eventType: str | None = None
    targetType: Literal["robot", "path", "station", "interface", "message", "resource"] = "robot"
    targetId: str | None = None
    recoveryMode: Literal[
        "manual",
        "auto",
        "retry",
        "reschedule",
        "skip_step",
        "takeover",
        "terminate_task",
    ] = "manual"
    reason: str = "operator recovery"
    operatorId: str = "simulation-console"


class MessageReplayCreate(BaseModel):
    replayMode: Literal["single", "task", "time_window"] = "single"
    sandbox: bool = True
    reason: str = "operator replay"
    operatorId: str = "simulation-console"


class MessageReplayResponse(BaseModel):
    replayId: str
    runId: str
    replayMode: str
    sandbox: bool
    message: MessageRecord
    observation: Observation | None = None


class SnapshotCreate(BaseModel):
    reason: str = "manual"


class Snapshot(BaseModel):
    snapshotId: str
    runId: str
    taskId: str | None = None
    traceId: str | None = None
    stateVersion: int
    reason: str
    snapshot: dict[str, Any]
    checksum: str
    createdAt: str


class TraceSpan(BaseModel):
    spanId: str
    parentSpanId: str | None = None
    traceId: str
    runId: str
    taskId: str | None = None
    entityType: str
    entityId: str
    operation: str
    status: str
    startedAt: str
    finishedAt: str | None = None
    durationMs: int | None = None
    inputRef: str | None = None
    outputRef: str | None = None
    errorRef: str | None = None


class TraceResponse(BaseModel):
    traceId: str
    runId: str | None = None
    taskId: str | None = None
    status: str
    startedAt: str | None = None
    finishedAt: str | None = None
    durationMs: int | None = None
    spans: list[TraceSpan] = Field(default_factory=list)
