from __future__ import annotations

import json
from threading import RLock
from copy import deepcopy
from pathlib import Path
from typing import Any

from .schemas import MessageRecord, RobotState, SiteMap, new_id, utc_now


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
STATE_PATH = DATA_DIR / "state.json"
EXPORT_DIR = DATA_DIR / "exports"


DEFAULT_MAP: dict[str, Any] = {
    "id": "site-a",
    "name": "Site A Sorting Area",
    "width": 1200,
    "height": 760,
    "unit": "mm",
    "gridSize": 40,
    "configVersion": "v0.1.0",
    "objects": [
        {
            "id": "zone-1",
            "type": "zone",
            "name": "Work Zone",
            "x": 360,
            "y": 260,
            "width": 460,
            "height": 280,
            "color": "#dbeafe",
        },
        {
            "id": "obstacle-1",
            "type": "obstacle",
            "name": "Safety Fence",
            "x": 610,
            "y": 220,
            "width": 130,
            "height": 80,
            "color": "#e5e7eb",
        },
        {
            "id": "station-1",
            "type": "station",
            "name": "Pick Station",
            "x": 220,
            "y": 240,
            "radius": 22,
            "color": "#dcfce7",
        },
        {
            "id": "station-2",
            "type": "station",
            "name": "Sort Station",
            "x": 760,
            "y": 420,
            "radius": 22,
            "color": "#dcfce7",
        },
        {
            "id": "pathNode-1",
            "type": "pathNode",
            "name": "P1",
            "x": 220,
            "y": 360,
            "radius": 7,
            "color": "#111827",
        },
        {
            "id": "pathNode-2",
            "type": "pathNode",
            "name": "P2",
            "x": 520,
            "y": 360,
            "radius": 7,
            "color": "#111827",
        },
        {
            "id": "pathNode-3",
            "type": "pathNode",
            "name": "P3",
            "x": 760,
            "y": 420,
            "radius": 7,
            "color": "#111827",
        },
        {
            "id": "resource-1",
            "type": "resourcePoint",
            "name": "Charge Point",
            "x": 960,
            "y": 160,
            "width": 36,
            "height": 36,
            "color": "#fef3c7",
        },
    ],
    "pathEdges": [
        {
            "id": "edge-1",
            "from": "pathNode-1",
            "to": "pathNode-2",
            "direction": "two_way",
            "capacity": 1,
        },
        {
            "id": "edge-2",
            "from": "pathNode-2",
            "to": "pathNode-3",
            "direction": "two_way",
            "capacity": 1,
        },
    ],
}


def default_state() -> dict[str, Any]:
    now = utc_now()
    return {
        "map": DEFAULT_MAP,
        "drafts": {},
        "robots": [
            {
                "robotId": "robot-001",
                "robotType": "machine-dog",
                "state": "Idle",
                "x": 220,
                "y": 360,
                "progress": 0,
                "currentAction": "Waiting for command",
                "updatedAt": now,
            }
        ],
        "messages": [
            {
                "messageId": new_id("msg"),
                "messageType": "system",
                "source": "platform-api",
                "topic": "api/startup",
                "createdAt": now,
                "payload": {"status": "ready"},
            }
        ],
        "auditLogs": [],
    }


class JsonStore:
    def __init__(self, state_path: Path = STATE_PATH):
        self.state_path = state_path
        self._lock = RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.write(default_state())

    def read(self) -> dict[str, Any]:
        with self._lock:
            with self.state_path.open("r", encoding="utf-8") as file:
                return json.load(file)

    def write(self, state: dict[str, Any]) -> None:
        with self._lock:
            tmp_path = self.state_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
            tmp_path.replace(self.state_path)

    def current_map(self) -> SiteMap:
        return SiteMap.model_validate(self.read()["map"])

    def save_draft(self, map_data: SiteMap) -> str:
        state = self.read()
        draft_id = new_id("draft")
        state["drafts"][draft_id] = {
            "draftId": draft_id,
            "map": map_data.model_dump(by_alias=True),
            "status": "editing",
            "createdAt": utc_now(),
        }
        self.append_audit_to_state(
            state,
            "map.draft.created",
            "map",
            map_data.id,
            after={"draftId": draft_id, "configVersion": map_data.configVersion},
        )
        self.write(state)
        return draft_id

    def draft_map(self, draft_id: str) -> SiteMap | None:
        draft = self.read()["drafts"].get(draft_id)
        if not draft:
            return None
        return SiteMap.model_validate(draft["map"])

    def validate_map(self, map_data: SiteMap) -> list[str]:
        issues: list[str] = []
        object_ids = [item.id for item in map_data.objects]
        ids = set(object_ids)
        edge_ids = [edge.id for edge in map_data.pathEdges]
        path_node_ids = {item.id for item in map_data.objects if item.type == "pathNode"}
        if map_data.width <= 0 or map_data.height <= 0:
            issues.append("map width and height must be positive")
        if map_data.gridSize <= 0:
            issues.append("gridSize must be positive")
        for object_id in sorted({item_id for item_id in object_ids if object_ids.count(item_id) > 1}):
            issues.append(f"object id {object_id} is duplicated")
        for edge_id in sorted({item_id for item_id in edge_ids if edge_ids.count(item_id) > 1}):
            issues.append(f"path edge id {edge_id} is duplicated")
        for item in map_data.objects:
            bounds = self._object_bounds(item)
            if (
                bounds["left"] < 0
                or bounds["right"] > map_data.width
                or bounds["top"] < 0
                or bounds["bottom"] > map_data.height
            ):
                issues.append(f"{item.id} is outside map bounds")
            if item.type in {"zone", "obstacle"} and ((item.width or 0) <= 0 or (item.height or 0) <= 0):
                issues.append(f"{item.id} must have positive width and height")
            if item.type in {"station", "pathNode"} and (item.radius or 0) < 0:
                issues.append(f"{item.id} radius must not be negative")
        for edge in map_data.pathEdges:
            if edge.from_ not in ids or edge.to not in ids:
                issues.append(f"{edge.id} references missing path node")
            if edge.from_ == edge.to:
                issues.append(f"{edge.id} must not connect a path node to itself")
            if edge.from_ in ids and edge.from_ not in path_node_ids:
                issues.append(f"{edge.id} from must reference a pathNode")
            if edge.to in ids and edge.to not in path_node_ids:
                issues.append(f"{edge.id} to must reference a pathNode")
            if edge.capacity < 1:
                issues.append(f"{edge.id} capacity must be greater than zero")
        issues.extend(self._validate_path_connectivity(path_node_ids, map_data))
        issues.extend(self._validate_collisions(map_data))
        return issues

    def storage_health(self) -> dict[str, Any]:
        try:
            state = self.read()
            return {
                "status": "ok",
                "statePath": str(self.state_path),
                "messageCount": len(state.get("messages", [])),
                "robotCount": len(state.get("robots", [])),
                "draftCount": len(state.get("drafts", {})),
            }
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "statePath": str(self.state_path),
                "error": str(exc),
            }

    def runtime_cache_health(self) -> dict[str, Any]:
        return {"status": "disabled"}

    def last_heartbeat_at(self) -> str | None:
        state = self.read()
        for message in reversed(state.get("messages", [])):
            payload = message.get("payload", {})
            if message.get("messageType") == "heartbeat" or payload.get("event") == "pose.updated":
                return message.get("createdAt")
        return None

    def publish_draft(self, draft_id: str) -> SiteMap | None:
        state = self.read()
        draft = state["drafts"].get(draft_id)
        if not draft:
            return None
        next_map = deepcopy(draft["map"])
        next_map["configVersion"] = f"v{utc_now().replace(':', '').replace('-', '')[:15]}"
        state["map"] = next_map
        state["drafts"][draft_id]["status"] = "published"
        state["drafts"][draft_id]["publishedAt"] = utc_now()
        self.append_audit_to_state(
            state,
            "map.draft.published",
            "map",
            next_map["id"],
            after={"draftId": draft_id, "configVersion": next_map["configVersion"]},
        )
        self.write(state)
        return SiteMap.model_validate(next_map)

    def robots(self) -> list[RobotState]:
        return [RobotState.model_validate(item) for item in self.read()["robots"]]

    def runtime_robots(self) -> list[RobotState]:
        return self.robots()

    def messages(self, limit: int = 100) -> list[MessageRecord]:
        records = self.read()["messages"]
        return [MessageRecord.model_validate(item) for item in records[-limit:]][::-1]

    def runtime_messages(self, limit: int = 100) -> list[MessageRecord]:
        return self.messages(limit)

    def query_messages(
        self,
        limit: int = 100,
        message_type: str | None = None,
        robot_code: str | None = None,
        command_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        request_id: str | None = None,
        event: str | None = None,
        topic: str | None = None,
        source: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> list[MessageRecord]:
        records = self.read()["messages"]
        filtered = [
            record
            for record in records
            if self._message_matches(
                record,
                message_type=message_type,
                robot_code=robot_code,
                command_id=command_id,
                trace_id=trace_id,
                task_id=task_id,
                request_id=request_id,
                event=event,
                topic=topic,
                source=source,
                created_from=created_from,
                created_to=created_to,
            )
        ]
        return [MessageRecord.model_validate(item) for item in filtered[-limit:]][::-1]

    def command_trace(self, command_id: str) -> list[MessageRecord]:
        records = self.read().get("messages", [])
        matched: list[MessageRecord] = []
        for record in records:
            payload = record.get("payload", {})
            inner_payload = payload.get("payload", {})
            if (
                payload.get("commandId") == command_id
                or payload.get("correlationId") == command_id
                or payload.get("traceId") == command_id
                or payload.get("taskId") == command_id
                or payload.get("requestId") == command_id
                or inner_payload.get("commandId") == command_id
            ):
                matched.append(MessageRecord.model_validate(record))
        return matched

    def recent_runtime_summary(self) -> dict[str, Any]:
        messages = self.read().get("messages", [])
        return {
            "lastCommand": self._last_message_by_type(messages, "command"),
            "lastEvent": self._last_message_by_type(messages, "event"),
            "lastError": self._last_error_message(messages),
        }

    def append_message(self, message: MessageRecord) -> None:
        state = self.read()
        state["messages"].append(message.model_dump())
        state["messages"] = state["messages"][-1000:]
        self.write(state)

    def upsert_robot_state(self, robot: RobotState) -> None:
        state = self.read()
        robots = [item for item in state["robots"] if item["robotId"] != robot.robotId]
        robots.append(robot.model_dump())
        state["robots"] = robots
        self.write(state)

    def create_export(self, export_type: str) -> tuple[str, str]:
        state = self.read()
        export_id = new_id("export")
        file_name = f"{export_id}-{export_type}.json"
        payload = {
            "exportId": export_id,
            "exportType": export_type,
            "createdAt": utc_now(),
            "data": state,
        }
        file_path = EXPORT_DIR / file_name
        with file_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        self.append_audit(
            "export.created",
            "export",
            export_id,
            after={"exportType": export_type, "fileName": file_name},
        )
        return export_id, file_name

    def append_audit(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        state = self.read()
        self.append_audit_to_state(state, action, resource_type, resource_id, before, after)
        self.write(state)

    @staticmethod
    def append_audit_to_state(
        state: dict[str, Any],
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        state["auditLogs"].append(
            {
                "id": new_id("audit"),
                "actorId": "system",
                "actorType": "service",
                "action": action,
                "resourceType": resource_type,
                "resourceId": resource_id,
                "before": before,
                "after": after,
                "createdAt": utc_now(),
            }
        )

    @staticmethod
    def _object_bounds(item: Any) -> dict[str, float]:
        if item.type in {"zone", "obstacle"}:
            half_width = (item.width or 0) / 2
            half_height = (item.height or 0) / 2
            return {
                "left": item.x - half_width,
                "right": item.x + half_width,
                "top": item.y - half_height,
                "bottom": item.y + half_height,
            }
        if item.type == "resourcePoint":
            half_width = (item.width or 34) / 2
            half_height = (item.height or item.width or 34) / 2
            return {
                "left": item.x - half_width,
                "right": item.x + half_width,
                "top": item.y - half_height,
                "bottom": item.y + half_height,
            }
        radius = item.radius or 0
        return {
            "left": item.x - radius,
            "right": item.x + radius,
            "top": item.y - radius,
            "bottom": item.y + radius,
        }

    @staticmethod
    def _boxes_overlap(first: dict[str, float], second: dict[str, float]) -> bool:
        return not (
            first["right"] <= second["left"]
            or first["left"] >= second["right"]
            or first["bottom"] <= second["top"]
            or first["top"] >= second["bottom"]
        )

    def _validate_collisions(self, map_data: SiteMap) -> list[str]:
        issues: list[str] = []
        collidable = [item for item in map_data.objects if item.type in {"obstacle", "station", "resourcePoint"}]
        for index, first in enumerate(collidable):
            for second in collidable[index + 1 :]:
                if first.type == "station" and second.type == "station":
                    continue
                if self._boxes_overlap(self._object_bounds(first), self._object_bounds(second)):
                    issues.append(f"{first.id} collides with {second.id}")
        return issues

    @staticmethod
    def _validate_path_connectivity(path_node_ids: set[str], map_data: SiteMap) -> list[str]:
        if len(path_node_ids) <= 1:
            return []
        adjacency = {node_id: set() for node_id in path_node_ids}
        for edge in map_data.pathEdges:
            if edge.from_ in path_node_ids and edge.to in path_node_ids:
                adjacency[edge.from_].add(edge.to)
                adjacency[edge.to].add(edge.from_)
        isolated = sorted(node_id for node_id, links in adjacency.items() if not links)
        issues = [f"path node {node_id} is disconnected" for node_id in isolated]
        if isolated:
            return issues
        visited: set[str] = set()
        stack = [next(iter(path_node_ids))]
        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            stack.extend(adjacency[node_id] - visited)
        for node_id in sorted(path_node_ids - visited):
            issues.append(f"path node {node_id} is not connected to the main path graph")
        return issues

    @staticmethod
    def _last_message_by_type(messages: list[dict[str, Any]], message_type: str) -> dict[str, Any] | None:
        for message in reversed(messages):
            if message.get("messageType") == message_type:
                return message
        return None

    @staticmethod
    def _last_error_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        for message in reversed(messages):
            payload = message.get("payload", {})
            inner_payload = payload.get("payload", {})
            if message.get("messageType", "").endswith("error"):
                return message
            if payload.get("error"):
                return message
            if payload.get("event") in {"command.rejected", "task.failed", "task.timeout", "where.failed", "device.offline"}:
                return message
            if inner_payload.get("severity") in {"error", "critical"}:
                return message
        return None

    @staticmethod
    def _message_matches(
        record: dict[str, Any],
        message_type: str | None,
        robot_code: str | None,
        command_id: str | None,
        trace_id: str | None,
        task_id: str | None,
        request_id: str | None,
        event: str | None,
        topic: str | None,
        source: str | None,
        created_from: str | None,
        created_to: str | None,
    ) -> bool:
        payload = record.get("payload", {})
        inner_payload = payload.get("payload", {})
        created_at = record.get("createdAt", "")
        if message_type and record.get("messageType") != message_type:
            return False
        if robot_code and robot_code not in {
            payload.get("robotCode"),
            payload.get("robotId"),
            inner_payload.get("robotCode"),
            inner_payload.get("robotId"),
        }:
            return False
        if command_id and command_id not in {
            payload.get("commandId"),
            payload.get("correlationId"),
            inner_payload.get("commandId"),
        }:
            return False
        if trace_id and payload.get("traceId") != trace_id:
            return False
        if task_id and payload.get("taskId") != task_id:
            return False
        if request_id and payload.get("requestId") != request_id:
            return False
        if event and payload.get("event") != event:
            return False
        if topic and topic not in record.get("topic", ""):
            return False
        if source and record.get("source") != source and payload.get("source") != source:
            return False
        if created_from and created_at < created_from:
            return False
        if created_to and created_at > created_to:
            return False
        return True
