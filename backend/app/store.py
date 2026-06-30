from __future__ import annotations

import json
from threading import RLock
from copy import deepcopy
from pathlib import Path
from typing import Any

from .schemas import (
    ExecutorInstance,
    ExecutorInstanceCreate,
    RobotConfig,
    RobotConfigCreate,
    RobotConfigUpdate,
    RobotState,
    TargetRegistryItem,
    TargetRegistryItemCreate,
    TargetRegistryItemUpdate,
    MessageRecord,
    SiteMap,
    action_command_names,
    new_id,
    utc_now,
    validate_action_params,
)


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
            "pathGroupId": "path-group-a",
            "sequence": 1,
        },
        {
            "id": "edge-2",
            "from": "pathNode-2",
            "to": "pathNode-3",
            "direction": "two_way",
            "capacity": 1,
            "pathGroupId": "path-group-b",
            "sequence": 1,
        },
    ],
    "pathGroups": [
        {
            "id": "path-group-a",
            "name": "Robot A Path",
            "edgeIds": ["edge-1"],
            "allowedRobotCodes": ["robot-001"],
            "color": "#2563eb",
            "status": "active",
            "priority": 5,
            "metadata": {"source": "seed"},
        },
        {
            "id": "path-group-b",
            "name": "Robot B Path",
            "edgeIds": ["edge-2"],
            "allowedRobotCodes": ["robot-002"],
            "color": "#16a34a",
            "status": "active",
            "priority": 5,
            "metadata": {"source": "seed"},
        },
    ],
}


def default_robot_states(now: str | None = None) -> list[dict[str, Any]]:
    timestamp = now or utc_now()
    return [
        {
            "robotId": "robot-001",
            "robotType": "machine-dog",
            "state": "Idle",
            "x": 220,
            "y": 360,
            "progress": 0,
            "currentAction": "Waiting for command",
            "updatedAt": timestamp,
        },
        {
            "robotId": "robot-002",
            "robotType": "machine-dog",
            "state": "Idle",
            "x": 320,
            "y": 360,
            "progress": 0,
            "currentAction": "Waiting for command",
            "updatedAt": timestamp,
        },
        {
            "robotId": "robot-003",
            "robotType": "machine-dog",
            "state": "Idle",
            "x": 420,
            "y": 360,
            "progress": 0,
            "currentAction": "Waiting for command",
            "updatedAt": timestamp,
        },
    ]


def map_with_default_path_groups(map_data: dict[str, Any] | SiteMap) -> dict[str, Any]:
    map_json = map_data.model_dump(by_alias=True) if isinstance(map_data, SiteMap) else deepcopy(map_data)
    if map_json.get("pathGroups"):
        return map_json

    path_edges = map_json.get("pathEdges", [])
    edge_ids = [edge.get("id") for edge in path_edges if edge.get("id")]
    if not edge_ids:
        map_json["pathGroups"] = []
        return map_json

    group_by_edge: dict[str, tuple[str, int]] = {}
    path_groups: list[dict[str, Any]] = []
    if map_json.get("id") == DEFAULT_MAP.get("id"):
        existing_edge_ids = set(edge_ids)
        for group in DEFAULT_MAP.get("pathGroups", []):
            group_edge_ids = [edge_id for edge_id in group.get("edgeIds", []) if edge_id in existing_edge_ids]
            if not group_edge_ids:
                continue
            next_group = deepcopy(group)
            next_group["edgeIds"] = group_edge_ids
            path_groups.append(next_group)
            for sequence, edge_id in enumerate(group_edge_ids, start=1):
                group_by_edge[edge_id] = (str(group["id"]), sequence)

    if not path_groups and len(edge_ids) > 1:
        color_palette = ["#2563eb", "#16a34a", "#dc2626", "#7c3aed", "#0891b2", "#d97706"]
        for index, edge_id in enumerate(edge_ids):
            group_id = f"path-group-{index + 1}"
            robot_code = f"robot-{index + 1:03d}" if index < 3 else None
            path_groups.append(
                {
                    "id": group_id,
                    "name": f"Path Group {index + 1}",
                    "edgeIds": [edge_id],
                    "allowedRobotCodes": [robot_code] if robot_code else [],
                    "color": color_palette[index % len(color_palette)],
                    "status": "active",
                    "priority": 5,
                    "metadata": {"source": "legacy-segmented"},
                }
            )
            group_by_edge[edge_id] = (group_id, 1)

    if not path_groups:
        path_groups = [
            {
                "id": "default-path-group",
                "name": "Default Path",
                "edgeIds": edge_ids,
                "allowedRobotCodes": [],
                "color": "#111827",
                "status": "active",
                "priority": 5,
                "metadata": {"source": "legacy"},
            }
        ]
        group_by_edge = {edge_id: ("default-path-group", sequence) for sequence, edge_id in enumerate(edge_ids, start=1)}

    for edge in path_edges:
        edge_id = edge.get("id")
        group = group_by_edge.get(edge_id)
        if not group:
            continue
        if not edge.get("pathGroupId"):
            edge["pathGroupId"] = group[0]
        if edge.get("sequence") is None:
            edge["sequence"] = group[1]

    map_json["pathGroups"] = path_groups
    return map_json


def default_targets_from_map(map_data: dict[str, Any] | SiteMap, now: str | None = None) -> list[dict[str, Any]]:
    timestamp = now or utc_now()
    map_json = map_with_default_path_groups(map_data)
    map_id = str(map_json.get("id") or "site-a")
    targets: list[dict[str, Any]] = [
        {
            "targetId": "cargo-001",
            "targetType": "cargo",
            "displayName": "Cargo 001",
            "mapId": map_id,
            "pose": {"x": 220, "y": 240, "z": 0, "yaw": 0},
            "geometryRef": "station-1",
            "metadata": {"source": "seed", "compatibleActions": ["pick", "load"]},
            "status": "active",
            "version": "v1",
            "createdAt": timestamp,
            "updatedAt": timestamp,
        },
        {
            "targetId": "container-001",
            "targetType": "container",
            "displayName": "Container 001",
            "mapId": map_id,
            "pose": {"x": 760, "y": 420, "z": 0, "yaw": 0},
            "geometryRef": "station-2",
            "metadata": {"source": "seed", "compatibleActions": ["place", "unload"]},
            "status": "active",
            "version": "v1",
            "createdAt": timestamp,
            "updatedAt": timestamp,
        },
        {
            "targetId": "inspection-001",
            "targetType": "inspectionPoint",
            "displayName": "Inspection Point 001",
            "mapId": map_id,
            "pose": {"x": 520, "y": 360, "z": 0, "yaw": 0},
            "geometryRef": "pathNode-2",
            "metadata": {"source": "seed", "compatibleActions": ["inspect", "goto_pose"]},
            "status": "active",
            "version": "v1",
            "createdAt": timestamp,
            "updatedAt": timestamp,
        },
    ]
    object_type_map = {
        "station": "station",
        "resourcePoint": "resource",
        "zone": "zone",
        "pathNode": "pathNode",
        "obstacle": "mapObject",
    }
    for item in map_json.get("objects", []):
        target_type = object_type_map.get(item.get("type"), "mapObject")
        targets.append(
            {
                "targetId": item["id"],
                "targetType": target_type,
                "displayName": item.get("name") or item["id"],
                "mapId": map_id,
                "pose": {"x": item.get("x", 0), "y": item.get("y", 0), "z": 0, "yaw": 0},
                "geometryRef": item["id"],
                "metadata": {"mapObjectType": item.get("type"), "source": "map"},
                "status": "active",
                "version": str(map_json.get("configVersion") or "v1"),
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
        )
    for edge in map_json.get("pathEdges", []):
        targets.append(
            {
                "targetId": edge["id"],
                "targetType": "pathEdge",
                "displayName": edge["id"],
                "mapId": map_id,
                "pose": None,
                "geometryRef": edge["id"],
                "metadata": {"from": edge.get("from"), "to": edge.get("to"), "capacity": edge.get("capacity", 1), "source": "map"},
                "status": "active",
                "version": str(map_json.get("configVersion") or "v1"),
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
        )
    for group in map_json.get("pathGroups", []):
        targets.append(
            {
                "targetId": group["id"],
                "targetType": "pathGroup",
                "displayName": group.get("name") or group["id"],
                "mapId": map_id,
                "pose": None,
                "geometryRef": group["id"],
                "metadata": {
                    "edgeIds": group.get("edgeIds", []),
                    "allowedRobotCodes": group.get("allowedRobotCodes", []),
                    "source": "map",
                },
                "status": "active" if group.get("status", "active") == "active" else "inactive",
                "version": str(map_json.get("configVersion") or "v1"),
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
        )
    deduped: dict[str, dict[str, Any]] = {}
    for target in targets:
        deduped[target["targetId"]] = target
    return list(deduped.values())


def map_source_targets_from_map(map_data: dict[str, Any] | SiteMap, now: str | None = None) -> list[dict[str, Any]]:
    return [target for target in default_targets_from_map(map_data, now) if target.get("metadata", {}).get("source") == "map"]


def sync_map_targets_in_state(state: dict[str, Any], map_data: dict[str, Any] | SiteMap) -> dict[str, int]:
    timestamp = utc_now()
    map_json = map_with_default_path_groups(map_data)
    map_id = str(map_json.get("id") or "site-a")
    desired_targets = map_source_targets_from_map(map_json, timestamp)
    desired_ids = {target["targetId"] for target in desired_targets}
    targets = state.setdefault("targets", default_targets_from_map(map_json, timestamp))
    existing_by_id = {target.get("targetId"): target for target in targets}
    summary = {"created": 0, "updated": 0, "inactivated": 0}

    for target in desired_targets:
        existing = existing_by_id.get(target["targetId"])
        if existing is None:
            targets.append(target)
            existing_by_id[target["targetId"]] = target
            summary["created"] += 1
            continue
        created_at = existing.get("createdAt") or target["createdAt"]
        if any(existing.get(key) != target.get(key) for key in ("targetType", "displayName", "mapId", "pose", "geometryRef", "metadata", "status", "version")):
            summary["updated"] += 1
        existing.update(target)
        existing["createdAt"] = created_at
        existing["updatedAt"] = timestamp

    for target in targets:
        if target.get("targetId") in desired_ids:
            continue
        if target.get("mapId") != map_id or target.get("metadata", {}).get("source") != "map":
            continue
        if target.get("status") != "inactive":
            target["status"] = "inactive"
            target["updatedAt"] = timestamp
            summary["inactivated"] += 1

    return summary


def target_registry_sync_issues(map_data: dict[str, Any] | SiteMap, targets: list[TargetRegistryItem] | list[dict[str, Any]]) -> list[str]:
    map_json = map_with_default_path_groups(map_data)
    map_id = str(map_json.get("id") or "site-a")
    map_version = str(map_json.get("configVersion") or "v1")
    desired_by_id = {target["targetId"]: target for target in map_source_targets_from_map(map_json)}
    desired_ids = set(desired_by_id)
    normalized_targets = [
        target.model_dump() if isinstance(target, TargetRegistryItem) else target
        for target in targets
    ]
    map_source_targets = [
        target
        for target in normalized_targets
        if target.get("mapId") == map_id and target.get("metadata", {}).get("source") == "map"
    ]
    registered_map_targets = {target.get("targetId"): target for target in map_source_targets}
    missing_ids = sorted(desired_ids - set(registered_map_targets))
    out_of_sync_ids = sorted(
        target_id
        for target_id, target in registered_map_targets.items()
        if target_id in desired_ids
        and (
            target.get("version") != map_version
            or target.get("status") != desired_by_id[target_id].get("status")
        )
    )
    stale_ids = sorted(
        target.get("targetId")
        for target in map_source_targets
        if target.get("status") == "active"
        and (target.get("targetId") not in desired_ids or target.get("version") != map_version)
    )
    issues: list[str] = []
    if missing_ids:
        issues.append(f"target registry missing current map targets: {', '.join(missing_ids[:5])}")
    if out_of_sync_ids:
        issues.append(f"target registry has out-of-sync current map targets: {', '.join(out_of_sync_ids[:5])}")
    if stale_ids:
        issues.append(f"target registry has stale active map targets: {', '.join(stale_ids[:5])}")
    return issues


def default_robot_configs(now: str | None = None) -> list[dict[str, Any]]:
    timestamp = now or utc_now()
    return [
        {
            "robotCode": robot["robotId"],
            "robotName": robot["robotId"],
            "robotType": robot["robotType"],
            "status": "enabled",
            "enabled": True,
            "capabilities": action_command_names(),
            "actionSetId": "machine-dog-basic",
            "mapId": "site-a",
            "initialPose": {"x": robot["x"], "y": robot["y"], "z": 0, "yaw": 0},
            "createMode": "config_only",
            "executorEndpoint": None,
            "metadata": {"source": "seed"},
            "executorId": None,
            "executorStatus": None,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        for robot in default_robot_states(timestamp)
    ]


def default_executor_instances(now: str | None = None) -> list[dict[str, Any]]:
    timestamp = now or utc_now()
    return [
        {
            "executorId": f"exec-{robot['robotId']}",
            "robotCode": robot["robotId"],
            "executorType": "virtual",
            "status": "active",
            "mqttClientId": f"virtual-dog-{robot['robotId']}",
            "lastHeartbeatAt": None,
            "containerName": f"virtual-robot-runner-{robot['robotId']}",
            "gatewayEndpoint": None,
            "startedAt": timestamp,
            "updatedAt": timestamp,
            "metadata": {
                "ROBOT_CODE": robot["robotId"],
                "ROBOT_TYPE": robot["robotType"],
                "START_X": robot["x"],
                "START_Y": robot["y"],
            },
        }
        for robot in default_robot_states(timestamp)
    ]


def default_state() -> dict[str, Any]:
    now = utc_now()
    return {
        "map": DEFAULT_MAP,
        "drafts": {},
        "robots": default_robot_states(now),
        "robotConfigs": default_robot_configs(now),
        "targets": default_targets_from_map(DEFAULT_MAP, now),
        "executors": default_executor_instances(now),
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
        return SiteMap.model_validate(map_with_default_path_groups(self.read()["map"]))

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
        return SiteMap.model_validate(map_with_default_path_groups(draft["map"]))

    def validate_map(self, map_data: SiteMap) -> list[str]:
        issues: list[str] = []
        object_ids = [item.id for item in map_data.objects]
        ids = set(object_ids)
        edge_ids = [edge.id for edge in map_data.pathEdges]
        path_node_ids = {item.id for item in map_data.objects if item.type == "pathNode"}
        path_group_ids = [group.id for group in map_data.pathGroups]
        if map_data.width <= 0 or map_data.height <= 0:
            issues.append("map width and height must be positive")
        if map_data.gridSize <= 0:
            issues.append("gridSize must be positive")
        for object_id in sorted({item_id for item_id in object_ids if object_ids.count(item_id) > 1}):
            issues.append(f"object id {object_id} is duplicated")
        for edge_id in sorted({item_id for item_id in edge_ids if edge_ids.count(item_id) > 1}):
            issues.append(f"path edge id {edge_id} is duplicated")
        for group_id in sorted({item_id for item_id in path_group_ids if path_group_ids.count(item_id) > 1}):
            issues.append(f"path group id {group_id} is duplicated")
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
            if edge.pathGroupId and edge.pathGroupId not in set(path_group_ids):
                issues.append(f"{edge.id} references missing path group {edge.pathGroupId}")
            if edge.speedLimit is not None and edge.speedLimit <= 0:
                issues.append(f"{edge.id} speedLimit must be greater than zero")
        try:
            robot_codes = {robot.robotId for robot in self.robots()}
        except Exception:
            robot_codes = set()
        issues.extend(self._validate_path_groups(map_data, edge_ids, robot_codes))
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
        sync_summary = sync_map_targets_in_state(state, next_map)
        state["drafts"][draft_id]["status"] = "published"
        state["drafts"][draft_id]["publishedAt"] = utc_now()
        self.append_audit_to_state(
            state,
            "map.draft.published",
            "map",
            next_map["id"],
            after={"draftId": draft_id, "configVersion": next_map["configVersion"], "targetSync": sync_summary},
        )
        self.write(state)
        return SiteMap.model_validate(next_map)

    def robots(self) -> list[RobotState]:
        return [RobotState.model_validate(item) for item in self.read()["robots"]]

    def runtime_robots(self) -> list[RobotState]:
        return self.robots()

    def create_robot(self, robot: RobotState) -> RobotState:
        state = self.read()
        if any(item["robotId"] == robot.robotId for item in state["robots"]):
            raise ValueError(f"robotCode already exists: {robot.robotId}")
        state["robots"].append(robot.model_dump())
        state.setdefault("robotConfigs", []).append(
            RobotConfig(
                robotCode=robot.robotId,
                robotName=robot.robotId,
                robotType=robot.robotType,
                status="enabled",
                enabled=True,
                capabilities=action_command_names(),
                actionSetId="machine-dog-basic",
                mapId="site-a",
                initialPose={"x": robot.x, "y": robot.y, "z": 0, "yaw": 0},
                createMode="config_only",
                executorEndpoint=None,
                metadata={"source": "robot-api"},
                executorId=None,
                executorStatus=None,
                createdAt=robot.updatedAt,
                updatedAt=robot.updatedAt,
            ).model_dump()
        )
        self.append_audit_to_state(
            state,
            "robot.created",
            "robot",
            robot.robotId,
            after=robot.model_dump(),
        )
        self.write(state)
        return robot

    def list_targets(self, target_type: str | None = None, status: str | None = None) -> list[TargetRegistryItem]:
        state = self.read()
        state.setdefault("targets", default_targets_from_map(state.get("map", DEFAULT_MAP)))
        targets = state["targets"]
        if target_type:
            targets = [item for item in targets if item.get("targetType") == target_type]
        if status:
            targets = [item for item in targets if item.get("status") == status]
        return [TargetRegistryItem.model_validate(item) for item in targets]

    def get_target(self, target_id: str) -> TargetRegistryItem | None:
        return next((target for target in self.list_targets() if target.targetId == target_id), None)

    def create_target(self, request: TargetRegistryItemCreate) -> TargetRegistryItem:
        state = self.read()
        targets = state.setdefault("targets", default_targets_from_map(state.get("map", DEFAULT_MAP)))
        if any(item["targetId"] == request.targetId for item in targets):
            raise ValueError(f"targetId already exists: {request.targetId}")
        now = utc_now()
        target = TargetRegistryItem(**request.model_dump(), createdAt=now, updatedAt=now)
        targets.append(target.model_dump())
        self.append_audit_to_state(state, "target.created", "target", target.targetId, after=target.model_dump())
        self.write(state)
        return target

    def update_target(self, target_id: str, request: TargetRegistryItemUpdate) -> TargetRegistryItem | None:
        state = self.read()
        targets = state.setdefault("targets", default_targets_from_map(state.get("map", DEFAULT_MAP)))
        for index, item in enumerate(targets):
            if item["targetId"] != target_id:
                continue
            before = deepcopy(item)
            patch = request.model_dump(exclude_unset=True)
            item.update(patch)
            item["updatedAt"] = utc_now()
            targets[index] = item
            self.append_audit_to_state(state, "target.updated", "target", target_id, before=before, after=item)
            self.write(state)
            return TargetRegistryItem.model_validate(item)
        return None

    def delete_target(self, target_id: str) -> TargetRegistryItem | None:
        return self.update_target(target_id, TargetRegistryItemUpdate(status="deleted"))

    def resolve_action_params(self, command: str, params: dict[str, Any] | None) -> dict[str, Any]:
        normalized = validate_action_params(command, params)
        target_id = normalized.get("targetId") or normalized.get("stationId")
        if not target_id:
            return normalized
        target = self.get_target(str(target_id))
        if target is None or target.status != "active":
            raise ValueError(f"unknown or inactive targetId: {target_id}")
        if normalized.get("targetType") and normalized["targetType"] != target.targetType:
            raise ValueError(f"targetId {target_id} is {target.targetType}, not {normalized['targetType']}")
        normalized["targetId"] = target.targetId
        normalized["targetType"] = target.targetType
        normalized["targetName"] = target.displayName
        if target.pose:
            pose = target.pose.model_dump()
            if command == "goto_pose":
                normalized = {**normalized, **pose}
            else:
                normalized["targetPose"] = pose
        return validate_action_params(command, normalized)

    def get_path_group(self, path_group_id: str) -> dict[str, Any] | None:
        for group in self.current_map().pathGroups:
            if group.id == path_group_id:
                return group.model_dump()
        return None

    def validate_robot_path_group(self, robot_code: str, path_group_id: str | None) -> None:
        if not path_group_id:
            return
        group = self.get_path_group(path_group_id)
        if group is None:
            raise ValueError(f"unknown pathGroupId: {path_group_id}")
        if group.get("status") != "active":
            raise ValueError(f"pathGroupId {path_group_id} is not active")
        allowed = set(group.get("allowedRobotCodes") or [])
        if allowed and robot_code not in allowed:
            raise ValueError(f"robot {robot_code} is not allowed to use pathGroupId {path_group_id}")

    def list_robot_configs(self) -> list[RobotConfig]:
        state = self.read()
        state.setdefault("robotConfigs", default_robot_configs())
        return [RobotConfig.model_validate(item) for item in state["robotConfigs"]]

    def get_robot_config(self, robot_code: str) -> RobotConfig | None:
        return next((robot for robot in self.list_robot_configs() if robot.robotCode == robot_code), None)

    def create_robot_config(self, request: RobotConfigCreate) -> RobotConfig:
        state = self.read()
        configs = state.setdefault("robotConfigs", default_robot_configs())
        if any(item["robotCode"] == request.robotCode for item in configs):
            raise ValueError(f"robotCode already exists: {request.robotCode}")
        now = utc_now()
        executor_id: str | None = None
        executor_status: str | None = None
        if request.createMode in {"start_virtual_executor", "bind_real_gateway"}:
            executor = self.create_executor(
                ExecutorInstanceCreate(
                    robotCode=request.robotCode,
                    executorType="virtual" if request.createMode == "start_virtual_executor" else "real_gateway",
                    gatewayEndpoint=request.executorEndpoint,
                    robotType=request.robotType,
                    startPose=request.initialPose,
                )
            )
            executor_id = executor.executorId
            executor_status = executor.status
            state = self.read()
            configs = state.setdefault("robotConfigs", default_robot_configs())
        config = RobotConfig(**request.model_dump(), executorId=executor_id, executorStatus=executor_status, createdAt=now, updatedAt=now)
        configs.append(config.model_dump())
        if not any(item["robotId"] == request.robotCode for item in state.setdefault("robots", [])):
            state["robots"].append(
                RobotState(
                    robotId=request.robotCode,
                    robotType=request.robotType,
                    state="Idle" if request.enabled else "Disabled",
                    x=request.initialPose.x,
                    y=request.initialPose.y,
                    progress=0,
                    currentAction="Waiting for command",
                    updatedAt=now,
                ).model_dump()
            )
        self.append_audit_to_state(state, "robot.config.created", "robot", request.robotCode, after=config.model_dump())
        self.write(state)
        return config

    def update_robot_config(self, robot_code: str, request: RobotConfigUpdate) -> RobotConfig | None:
        state = self.read()
        configs = state.setdefault("robotConfigs", default_robot_configs())
        for index, item in enumerate(configs):
            if item["robotCode"] != robot_code:
                continue
            before = deepcopy(item)
            item.update(request.model_dump(exclude_unset=True))
            item["updatedAt"] = utc_now()
            configs[index] = item
            self.append_audit_to_state(state, "robot.config.updated", "robot", robot_code, before=before, after=item)
            self.write(state)
            return RobotConfig.model_validate(item)
        return None

    def delete_robot_config(self, robot_code: str) -> RobotConfig | None:
        return self.update_robot_config(robot_code, RobotConfigUpdate(status="deleted", enabled=False))

    def list_executors(self, robot_code: str | None = None) -> list[ExecutorInstance]:
        state = self.read()
        state.setdefault("executors", default_executor_instances())
        executors = state["executors"]
        if robot_code:
            executors = [item for item in executors if item.get("robotCode") == robot_code]
        return [ExecutorInstance.model_validate(item) for item in executors]

    def get_executor(self, executor_id: str) -> ExecutorInstance | None:
        return next((executor for executor in self.list_executors() if executor.executorId == executor_id), None)

    def create_executor(self, request: ExecutorInstanceCreate) -> ExecutorInstance:
        state = self.read()
        executors = state.setdefault("executors", default_executor_instances())
        active = [item for item in executors if item.get("robotCode") == request.robotCode and item.get("status") == "active"]
        if active:
            raise ValueError(f"robot {request.robotCode} already has active executor {active[0]['executorId']}")
        now = utc_now()
        executor_id = new_id("exec")
        executor = ExecutorInstance(
            executorId=executor_id,
            robotCode=request.robotCode,
            executorType=request.executorType,
            status="active" if request.executorType == "virtual" else "binding",
            mqttClientId=request.mqttClientId or f"{request.executorType}-{request.robotCode}",
            containerName=request.containerName or (f"virtual-robot-runner-{request.robotCode}" if request.executorType == "virtual" else None),
            gatewayEndpoint=request.gatewayEndpoint,
            startedAt=now,
            updatedAt=now,
            metadata={
                "ROBOT_CODE": request.robotCode,
                "ROBOT_TYPE": request.robotType,
                "START_X": request.startPose.x if request.startPose else None,
                "START_Y": request.startPose.y if request.startPose else None,
                **request.metadata,
            },
        )
        executors.append(executor.model_dump())
        self.append_audit_to_state(state, "executor.created", "executor", executor.executorId, after=executor.model_dump())
        self.write(state)
        return executor

    def transition_executor(self, executor_id: str, status: str) -> ExecutorInstance | None:
        state = self.read()
        executors = state.setdefault("executors", default_executor_instances())
        for index, item in enumerate(executors):
            if item["executorId"] != executor_id:
                continue
            before = deepcopy(item)
            item["status"] = status
            item["updatedAt"] = utc_now()
            if status == "active" and not item.get("startedAt"):
                item["startedAt"] = item["updatedAt"]
            executors[index] = item
            self.append_audit_to_state(state, f"executor.{status}", "executor", executor_id, before=before, after=item)
            self.write(state)
            return ExecutorInstance.model_validate(item)
        return None

    def executor_logs(self, executor_id: str, limit: int = 100) -> list[MessageRecord]:
        executor = self.get_executor(executor_id)
        if executor is None:
            return []
        return self.query_messages(limit=limit, robot_code=executor.robotCode)

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
    def _validate_path_groups(map_data: SiteMap, edge_ids: list[str], robot_codes: set[str]) -> list[str]:
        issues: list[str] = []
        if not map_data.pathGroups:
            return issues
        edge_by_id = {edge.id: edge for edge in map_data.pathEdges}
        grouped_edge_ids: set[str] = set()
        for group in map_data.pathGroups:
            if not group.name.strip():
                issues.append(f"path group {group.id} name is required")
            for robot_code in group.allowedRobotCodes:
                if robot_codes and robot_code not in robot_codes:
                    issues.append(f"path group {group.id} references missing robot {robot_code}")
            for edge_id in group.edgeIds:
                if edge_id not in edge_by_id:
                    issues.append(f"path group {group.id} references missing path edge {edge_id}")
                    continue
                grouped_edge_ids.add(edge_id)
                edge = edge_by_id[edge_id]
                if edge.pathGroupId and edge.pathGroupId != group.id:
                    issues.append(f"path edge {edge.id} belongs to {edge.pathGroupId}, not {group.id}")
            ordered_edges = [
                edge_by_id[edge_id]
                for edge_id in group.edgeIds
                if edge_id in edge_by_id
            ]
            if len(ordered_edges) <= 1:
                continue
            ordered_edges = sorted(
                ordered_edges,
                key=lambda edge: edge.sequence if edge.sequence is not None else group.edgeIds.index(edge.id),
            )
            previous = ordered_edges[0]
            for edge in ordered_edges[1:]:
                if previous.to != edge.from_:
                    issues.append(f"path group {group.id} is not continuous between {previous.id} and {edge.id}")
                previous = edge
        for edge_id in edge_ids:
            edge = edge_by_id[edge_id]
            if edge.pathGroupId and edge_id not in grouped_edge_ids:
                issues.append(f"path edge {edge_id} declares pathGroupId but is not listed in that group")
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
        if map_data.pathGroups:
            return issues
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
