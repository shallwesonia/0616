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
