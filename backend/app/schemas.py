from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


MapObjectType = Literal["zone", "obstacle", "station", "pathNode", "resourcePoint"]


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
    command: Literal["goto_pose", "where", "stop"]
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
