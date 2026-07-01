from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .schemas import (
    HubIdMapping,
    HubIntegrationStatus,
    HubSyncItem,
    HubSyncResponse,
    SimulationAction,
    SimulationPlan,
    SimulationRun,
    SimulationTask,
    TargetRegistryItem,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _hub_base_url() -> str:
    return os.getenv("HUB_BASE_URL", "http://127.0.0.1:8001/api/v1").rstrip("/")


def scene_timestamp_from_version(site_map_version: str | None) -> str:
    version = (site_map_version or "").strip()
    if version.startswith("v") and len(version) > 1:
        return version[1:]
    return version or "unknown"


def scene_name_for_scenario(scenario_id: str, site_map_version: str | None) -> str:
    return f"0616-{scenario_id}-{scene_timestamp_from_version(site_map_version)}"


def hub_mqtt_subscription_info() -> dict[str, Any]:
    public_host = os.getenv("PUBLIC_HOST", "localhost")
    mqtt_public_port = int(os.getenv("MQTT_PUBLIC_PORT", "18830"))
    return {
        "brokerHostFromHubContainer": os.getenv("HUB_MQTT_HOST", "host.docker.internal"),
        "brokerHostFromLan": public_host,
        "brokerPort": int(os.getenv("HUB_MQTT_PORT", str(mqtt_public_port))),
        "protocol": "MQTT 3.1.1",
        "qos": 0,
        "clientId": os.getenv("HUB_MQTT_CLIENT_ID", "scene-world-state-hub-adapter"),
        "topics": [
            {
                "name": "robotResult",
                "topic": "factory/dogs/+/result",
                "direction": "subscribe",
                "description": "Hub consumes robot executor result events from 0616.",
            }
        ],
        "eventMapping": {
            "Observation": ["pose.updated", "where.result", "action.progress"],
            "ExecutorResult": ["command.accepted", "command.rejected", "task.succeeded", "task.failed", "action.succeeded", "action.failed"],
            "Alert": ["device.offline", "robot.offline", "path.blocked", "interface.timeout", "message.dropped"],
        },
        "requiredPayloadFields": ["messageId", "messageType", "robotCode", "event", "timestamp", "scene_name"],
        "recommendedCorrelationFields": ["runId", "taskId", "actionId", "commandId", "requestId", "traceId"],
    }


class HubClientError(RuntimeError):
    def __init__(self, status_code: int | None, message: str):
        self.status_code = status_code
        super().__init__(message)


@dataclass
class HubClient:
    base_url: str
    enabled: bool
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "HubClient":
        return cls(
            base_url=_hub_base_url(),
            enabled=_env_bool("HUB_SYNC_ENABLED", False),
            timeout_seconds=float(os.getenv("HUB_TIMEOUT_SECONDS", "5")),
        )

    @property
    def health_url(self) -> str:
        if self.base_url.endswith("/api/v1"):
            return self.base_url[: -len("/api/v1")] + "/health"
        return self.base_url.rstrip("/") + "/health"

    def status(self) -> HubIntegrationStatus:
        if not self.enabled:
            return HubIntegrationStatus(
                enabled=False,
                baseUrl=self.base_url,
                healthUrl=self.health_url,
                status="disabled",
                mqttSubscription=hub_mqtt_subscription_info(),
            )
        try:
            self._request_url("GET", self.health_url)
            return HubIntegrationStatus(
                enabled=True,
                baseUrl=self.base_url,
                healthUrl=self.health_url,
                status="ok",
                mqttSubscription=hub_mqtt_subscription_info(),
            )
        except HubClientError as exc:
            return HubIntegrationStatus(
                enabled=True,
                baseUrl=self.base_url,
                healthUrl=self.health_url,
                status="error",
                error=str(exc),
                mqttSubscription=hub_mqtt_subscription_info(),
            )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        if not self.enabled:
            raise HubClientError(None, "Hub sync is disabled. Set HUB_SYNC_ENABLED=true to enable it.")
        return self._request_url(method, f"{self.base_url}{path}", payload)

    def _request_url(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method.upper(),
            headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise HubClientError(exc.code, detail or str(exc)) from exc
        except Exception as exc:
            raise HubClientError(None, str(exc)) from exc


class HubIntegrationService:
    def __init__(self, store: Any, client: HubClient | None = None):
        self.store = store
        self.client = client or HubClient.from_env()

    def status(self) -> HubIntegrationStatus:
        return self.client.status()

    def list_mappings(self, limit: int = 200) -> list[HubIdMapping]:
        if not hasattr(self.store, "list_hub_mappings"):
            return []
        return self.store.list_hub_mappings(limit=limit)

    def sync_scene(self, scenario_id: str, force: bool = False) -> HubSyncResponse:
        scenario = self.store.get_scenario(scenario_id)
        if scenario is None:
            return self._error("scene", f"scenario not found: {scenario_id}")
        items: list[HubSyncItem] = []
        try:
            self._sync_scene(scenario, items, force=force)
        except HubClientError as exc:
            return self._error("scene", str(exc), items)
        return self._response("scene", items)

    def sync_entities(self, scenario_id: str, force: bool = False) -> HubSyncResponse:
        scenario = self.store.get_scenario(scenario_id)
        if scenario is None:
            return self._error("entities", f"scenario not found: {scenario_id}")
        items: list[HubSyncItem] = []
        try:
            scene_mapping = self._sync_scene(scenario, items, force=force)
            self._sync_entities_for_scene(str(scene_mapping.hubId), items, force=force)
        except HubClientError as exc:
            return self._error("entities", str(exc), items)
        return self._response("entities", items)

    def sync_run_graph(self, run_id: str, force: bool = False) -> HubSyncResponse:
        items: list[HubSyncItem] = []
        try:
            run = self.store.get_simulation_run(run_id)
            if run is None:
                return self._error("run_graph", f"run not found: {run_id}")
            self._sync_run(run, items, force=force)
            for task in self.store.list_run_tasks(run_id):
                self._sync_task(task, items, force=force)
                if task.activePlan:
                    self._sync_plan(task.activePlan, items, force=force)
            for action in reversed(self.store.list_actions(run_id=run_id, limit=1000)):
                self._sync_action(action, items, force=force)
        except HubClientError as exc:
            return self._error("run_graph", str(exc), items)
        return self._response("run_graph", items)

    def sync_task(self, task_id: str, force: bool = False) -> HubSyncResponse:
        items: list[HubSyncItem] = []
        try:
            task = self.store.get_task(task_id)
            if task is None:
                return self._error("task", f"task not found: {task_id}")
            self._sync_task(task, items, force=force)
            if task.activePlan:
                self._sync_plan(task.activePlan, items, force=force)
        except HubClientError as exc:
            return self._error("task", str(exc), items)
        return self._response("task", items)

    def sync_plan(self, plan_id: str, force: bool = False) -> HubSyncResponse:
        items: list[HubSyncItem] = []
        try:
            plan = self.store.get_plan(plan_id) if hasattr(self.store, "get_plan") else None
            if plan is None:
                return self._error("plan", f"plan not found: {plan_id}")
            self._sync_plan(plan, items, force=force)
        except HubClientError as exc:
            return self._error("plan", str(exc), items)
        return self._response("plan", items)

    def sync_action(self, action_id: str, force: bool = False) -> HubSyncResponse:
        items: list[HubSyncItem] = []
        try:
            action = self.store.get_action(action_id)
            if action is None:
                return self._error("action", f"action not found: {action_id}")
            self._sync_action(action, items, force=force)
        except HubClientError as exc:
            return self._error("action", str(exc), items)
        return self._response("action", items)

    def _sync_scene(self, scenario: Any, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("scenario", scenario.scenarioId, "scene")
        scene_name = scene_name_for_scenario(scenario.scenarioId, scenario.siteMapVersion) if force else f"0616-{scenario.scenarioId}"
        payload = {
            "scene_name": scene_name,
            "description": f"0616 scenario: {scenario.name}",
            "map_config": scenario.map.model_dump(by_alias=True) if hasattr(scenario.map, "model_dump") else scenario.map,
            "metadata": {
                "sourcePlatform": "0616",
                "externalId": scenario.scenarioId,
                "siteMapId": scenario.siteMapId,
                "siteMapVersion": scenario.siteMapVersion,
                "scene_name": scene_name,
                "robotCodes": scenario.robotCodes,
            },
        }
        existing_scene = self._find_scene_by_name(scene_name, scenario.scenarioId)
        if existing_scene is not None:
            mapping = self._upsert_scene_mapping(
                scenario,
                str(existing_scene["id"]),
                scene_name,
                "reused_existing_no_update",
            )
            items.append(self._item(mapping, "reused", "Hub Scene already exists, v0.1 does not update map_config / metadata / description"))
            return mapping

        try:
            obj = self.client.request("POST", "/scenes", payload)
        except HubClientError as exc:
            if exc.status_code != 409:
                raise
            obj = self._find_scene_by_name(scene_name, scenario.scenarioId)
            if obj is None:
                raise
        previous_scene_name = None
        if existing:
            previous_scene_name = (existing.metadata or {}).get("scene_name") or (existing.metadata or {}).get("sceneName")
        sync_mode = "recreated_after_stale_mapping" if existing and previous_scene_name == scene_name else "created"
        mapping = self._upsert_scene_mapping(
            scenario,
            str(obj["id"]),
            scene_name,
            sync_mode,
        )
        detail = "Hub Scene recreated after stale local mapping" if sync_mode == "recreated_after_stale_mapping" else "Hub Scene registered"
        items.append(self._item(mapping, "synced", detail))
        return mapping

    def _upsert_scene_mapping(self, scenario: Any, hub_id: str, scene_name: str, sync_mode: str) -> HubIdMapping:
        return self._upsert(
            "scenario",
            scenario.scenarioId,
            "scene",
            hub_id,
            metadata={
                "sceneName": scene_name,
                "scene_name": scene_name,
                "sceneSyncMode": sync_mode,
                "siteMapVersion": scenario.siteMapVersion,
            },
        )

    def _sync_entities_for_scene(self, hub_scene_id: str, items: list[HubSyncItem], force: bool = False) -> None:
        for robot in self.store.robots():
            self._sync_robot_entity(hub_scene_id, robot, items, force=force)
        for target in self.store.list_targets(status="active") if hasattr(self.store, "list_targets") else []:
            self._sync_target_entity(hub_scene_id, target, items, force=force)

    def _sync_robot_entity(self, hub_scene_id: str, robot: Any, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("robot", robot.robotId, "entity")
        if existing and existing.hubId and not force:
            items.append(self._item(existing, "reused"))
            return existing
        payload = {
            "scene_id": hub_scene_id,
            "entity_name": robot.robotId,
            "entity_type": "robot",
            "properties": robot.model_dump() if hasattr(robot, "model_dump") else dict(robot),
            "metadata": {"sourcePlatform": "0616", "externalId": robot.robotId, "externalType": "robot"},
        }
        obj = self._create_or_find_entity(payload, robot.robotId)
        mapping = self._upsert("robot", robot.robotId, "entity", str(obj["id"]), metadata={"entityName": robot.robotId})
        items.append(self._item(mapping, "synced"))
        return mapping

    def _sync_target_entity(self, hub_scene_id: str, target: TargetRegistryItem, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("target", target.targetId, "entity")
        if existing and existing.hubId and not force:
            items.append(self._item(existing, "reused"))
            return existing
        payload = {
            "scene_id": hub_scene_id,
            "entity_name": target.targetId,
            "entity_type": target.targetType,
            "properties": {
                "displayName": target.displayName,
                "pose": target.pose.model_dump() if target.pose and hasattr(target.pose, "model_dump") else target.pose,
                "geometryRef": target.geometryRef,
                "mapId": target.mapId,
                "metadata": target.metadata,
            },
            "metadata": {"sourcePlatform": "0616", "externalId": target.targetId, "externalType": "target"},
        }
        obj = self._create_or_find_entity(payload, target.targetId)
        mapping = self._upsert("target", target.targetId, "entity", str(obj["id"]), metadata={"entityName": target.targetId})
        items.append(self._item(mapping, "synced"))
        return mapping

    def _sync_run(self, run: SimulationRun, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("run", run.runId, "run")
        if existing and existing.hubId and not force:
            items.append(self._item(existing, "reused"))
            return existing
        scenario = self.store.get_scenario(run.scenarioId)
        if scenario is None:
            raise HubClientError(None, f"scenario not found: {run.scenarioId}")
        scene_mapping = self._sync_scene(scenario, items, force=force)
        self._sync_entities_for_scene(str(scene_mapping.hubId), items, force=force)
        payload = {
            "run_id": run.runId,
            "scene_id": scene_mapping.hubId,
            "status": str(run.status).lower(),
            "phase": "executing" if run.status == "Running" else "observing",
            "metadata": {
                "sourcePlatform": "0616",
                "externalId": run.runId,
                "scenarioId": run.scenarioId,
                "mapId": run.mapId,
                "mapVersion": run.mapVersion,
            },
        }
        try:
            obj = self.client.request("POST", "/runs", payload)
        except HubClientError as exc:
            if exc.status_code != 409:
                raise
            obj = self.client.request("GET", f"/runs/{urllib.parse.quote(run.runId)}")
        mapping = self._upsert("run", run.runId, "run", str(obj["id"]), metadata={"runId": run.runId})
        items.append(self._item(mapping, "synced"))
        return mapping

    def _sync_task(self, task: SimulationTask, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("task", task.taskId, "task")
        if existing and existing.hubId and not force:
            items.append(self._item(existing, "reused"))
            self._sync_trace_for_task(task, str(existing.hubId), items, force=force)
            return existing
        run = self.store.get_simulation_run(task.runId)
        if run is None:
            raise HubClientError(None, f"run not found: {task.runId}")
        self._sync_run(run, items, force=force)
        scene_mapping = self._mapping("scenario", run.scenarioId, "scene")
        if scene_mapping is None or not scene_mapping.hubId:
            raise HubClientError(None, "Hub scene mapping is missing")
        payload = {
            "run_id": task.runId,
            "scene_id": scene_mapping.hubId,
            "task_name": task.goal,
            "task_type": str(task.input.get("taskType") or task.input.get("task_type") or "simulation_task"),
            "definition": {
                "input": task.input,
                "constraints": task.constraints,
                "expectedOutcome": task.expectedOutcome,
                "activePlanId": task.activePlan.planId if task.activePlan else None,
            },
            "final_review": None,
            "status": self._task_status(task.status),
            "source": task.createdBy,
            "metadata": {"sourcePlatform": "0616", "externalId": task.taskId, "externalTraceId": task.traceId},
        }
        obj = self.client.request("POST", "/tasks", payload)
        mapping = self._upsert("task", task.taskId, "task", str(obj["id"]), external_trace_id=task.traceId, metadata={"taskName": task.goal})
        items.append(self._item(mapping, "synced"))
        self._sync_trace_for_task(task, str(mapping.hubId), items, force=force)
        return mapping

    def _sync_plan(self, plan: SimulationPlan, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("plan", plan.planId, "plan")
        if existing and existing.hubId and not force:
            items.append(self._item(existing, "reused"))
            return existing
        task = self.store.get_task(plan.taskId)
        if task is None:
            raise HubClientError(None, f"task not found: {plan.taskId}")
        task_mapping = self._sync_task(task, items, force=force)
        payload = {
            "task_id": task_mapping.hubId,
            "version": plan.planVersion,
            "source": plan.generatedBy,
            "status": self._plan_status(plan.status),
            "plan_data": {"steps": [step.model_dump() for step in plan.steps]},
            "metadata": {
                "sourcePlatform": "0616",
                "externalId": plan.planId,
                "externalTraceId": plan.traceId,
                "strategy": plan.strategy,
                "dependencies": plan.dependencies,
                "assumptions": plan.assumptions,
            },
        }
        obj = self.client.request("POST", "/plans", payload)
        mapping = self._upsert("plan", plan.planId, "plan", str(obj["id"]), external_trace_id=plan.traceId, metadata={"version": plan.planVersion})
        items.append(self._item(mapping, "synced"))
        self._append_trace_event(plan.traceId, "plan.synced", mapping.hubId, "plan", {"planId": plan.planId})
        return mapping

    def _sync_action(self, action: SimulationAction, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("action", action.actionId, "action")
        if existing and (existing.hubId or existing.syncStatus == "skipped") and not force:
            items.append(self._item(existing, "reused" if existing.hubId else "skipped"))
            return existing
        if action.command == "where":
            mapping = self._upsert(
                "action",
                action.actionId,
                "action",
                None,
                external_trace_id=action.traceId,
                sync_status="skipped",
                metadata={"reason": "where is a query command and Hub rejects it as Action"},
            )
            self._append_trace_event(action.traceId, "action.query_skipped", None, "action", {"actionId": action.actionId, "command": action.command})
            items.append(self._item(mapping, "skipped", "where is stored as Trace event only"))
            return mapping
        if not action.taskId or not action.planId:
            raise HubClientError(None, f"action {action.actionId} must have taskId and planId before Hub sync")
        task = self.store.get_task(action.taskId)
        plan = self.store.get_plan(action.planId) if hasattr(self.store, "get_plan") else None
        if task is None or plan is None:
            raise HubClientError(None, "local task or plan not found")
        task_mapping = self._sync_task(task, items, force=force)
        plan_mapping = self._sync_plan(plan, items, force=force)
        robot_mapping = self._mapping("robot", action.robotCode, "entity")
        if robot_mapping is None or not robot_mapping.hubId:
            run = self.store.get_simulation_run(action.runId)
            if run is None:
                raise HubClientError(None, f"run not found: {action.runId}")
            scenario = self.store.get_scenario(run.scenarioId)
            if scenario is None:
                raise HubClientError(None, f"scenario not found: {run.scenarioId}")
            scene_mapping = self._sync_scene(scenario, items, force=force)
            robot = next((item for item in self.store.robots() if item.robotId == action.robotCode), None)
            if robot is None:
                raise HubClientError(None, f"robot not found: {action.robotCode}")
            robot_mapping = self._sync_robot_entity(str(scene_mapping.hubId), robot, items, force=force)
        payload = {
            "task_id": task_mapping.hubId,
            "plan_id": plan_mapping.hubId,
            "entity_id": robot_mapping.hubId,
            "action_type": action.command,
            "parameters": action.params,
            "preconditions": {},
            "postconditions": {},
            "status": self._action_status(action.status),
            "metadata": {
                "sourcePlatform": "0616",
                "externalId": action.actionId,
                "externalTraceId": action.traceId,
                "commandId": action.commandId,
                "requestId": action.requestId,
                "planStepId": action.planStepId,
                "robotCode": action.robotCode,
            },
        }
        obj = self.client.request("POST", "/actions", payload)
        mapping = self._upsert("action", action.actionId, "action", str(obj["id"]), external_trace_id=action.traceId, metadata={"command": action.command})
        items.append(self._item(mapping, "synced"))
        self._append_trace_event(action.traceId, "action.synced", mapping.hubId, "action", {"actionId": action.actionId, "command": action.command})
        return mapping

    def _sync_trace_for_task(self, task: SimulationTask, hub_task_id: str, items: list[HubSyncItem], force: bool = False) -> HubIdMapping:
        existing = self._mapping("trace", task.traceId, "trace")
        if existing and existing.hubId and not force:
            return existing
        run = self.store.get_simulation_run(task.runId)
        scene_mapping = self._mapping("scenario", run.scenarioId, "scene") if run else None
        payload = {
            "trace_type": "simulation_task",
            "owner_module": "0616-platform",
            "run_id": task.runId,
            "task_id": hub_task_id,
            "scene_id": scene_mapping.hubId if scene_mapping else None,
            "summary": task.goal,
            "metadata": {"sourcePlatform": "0616", "externalTraceId": task.traceId, "externalTaskId": task.taskId},
        }
        obj = self.client.request("POST", "/traces", payload)
        mapping = self._upsert(
            "trace",
            task.traceId,
            "trace",
            str(obj["id"]),
            external_id=task.traceId,
            external_trace_id=task.traceId,
            hub_trace_id=str(obj["id"]),
            metadata={"taskId": task.taskId},
        )
        items.append(self._item(mapping, "synced"))
        return mapping

    def _append_trace_event(
        self,
        external_trace_id: str,
        event_type: str,
        hub_ref_id: str | None,
        ref_type: str,
        event_data: dict[str, Any],
    ) -> None:
        mapping = self._mapping("trace", external_trace_id, "trace")
        if mapping is None or not mapping.hubId:
            return
        payload = {
            "event_type": event_type,
            "ref_id": hub_ref_id,
            "ref_type": ref_type,
            "event_data": {"sourcePlatform": "0616", "externalTraceId": external_trace_id, **event_data},
        }
        self.client.request("POST", f"/traces/{mapping.hubId}/events", payload)

    def _create_or_find_entity(self, payload: dict[str, Any], external_id: str) -> dict[str, Any]:
        try:
            return self.client.request("POST", "/entities", payload)  # type: ignore[return-value]
        except HubClientError as exc:
            if exc.status_code != 409:
                raise
            obj = self._find_entity(payload["scene_id"], payload["entity_name"], external_id)
            if obj is None:
                raise
            return obj

    def _find_scene_by_name(self, scene_name: str, external_id: str) -> dict[str, Any] | None:
        try:
            scene = self.client.request("GET", f"/scenes/by-name?scene_name={urllib.parse.quote(scene_name)}")
        except HubClientError as exc:
            if exc.status_code == 404:
                return None
            if exc.status_code not in {405, 422}:
                raise
        else:
            return scene if isinstance(scene, dict) else None
        return self._find_scene_from_list(scene_name, external_id)

    def _find_scene_from_list(self, scene_name: str, external_id: str) -> dict[str, Any] | None:
        scenes = self.client.request("GET", "/scenes")
        for scene in scenes if isinstance(scenes, list) else []:
            metadata = scene.get("metadata") or {}
            if scene.get("scene_name") == scene_name and metadata.get("externalId") in {None, external_id}:
                return scene
        return None

    def _find_entity(self, scene_id: str, entity_name: str, external_id: str) -> dict[str, Any] | None:
        entities = self.client.request("GET", f"/entities?scene_id={urllib.parse.quote(scene_id)}")
        for entity in entities if isinstance(entities, list) else []:
            metadata = entity.get("metadata") or {}
            if entity.get("entity_name") == entity_name and metadata.get("externalId") == external_id:
                return entity
        return None

    def _mapping(self, local_type: str, local_id: str, hub_type: str) -> HubIdMapping | None:
        if not hasattr(self.store, "get_hub_mapping"):
            return None
        return self.store.get_hub_mapping(local_type, local_id, hub_type)

    def _upsert(
        self,
        local_type: str,
        local_id: str,
        hub_type: str,
        hub_id: str | None,
        external_id: str | None = None,
        external_trace_id: str | None = None,
        hub_trace_id: str | None = None,
        sync_status: str = "synced",
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HubIdMapping:
        if not hasattr(self.store, "upsert_hub_mapping"):
            raise HubClientError(None, "Hub ID mapping requires database store")
        return self.store.upsert_hub_mapping(
            local_type=local_type,
            local_id=local_id,
            hub_type=hub_type,
            hub_id=hub_id,
            external_id=external_id or local_id,
            external_trace_id=external_trace_id,
            hub_trace_id=hub_trace_id,
            sync_status=sync_status,
            last_error=last_error,
            metadata=metadata or {},
        )

    def _response(self, stage: str, items: list[HubSyncItem]) -> HubSyncResponse:
        mappings = self.list_mappings(limit=200)
        return HubSyncResponse(ok=not any(item.status == "error" for item in items), stage=stage, items=items, mappings=mappings)

    def _error(self, stage: str, error: str, items: list[HubSyncItem] | None = None) -> HubSyncResponse:
        return HubSyncResponse(ok=False, stage=stage, items=items or [], mappings=self.list_mappings(limit=200), error=error)

    @staticmethod
    def _item(mapping: HubIdMapping, status: str, detail: str | None = None) -> HubSyncItem:
        return HubSyncItem(
            localType=mapping.localType,
            localId=mapping.localId,
            hubType=mapping.hubType,
            hubId=mapping.hubId,
            status=status,  # type: ignore[arg-type]
            detail=detail or mapping.lastError,
        )

    @staticmethod
    def _task_status(status: str) -> str:
        return {
            "Ready": "pending",
            "Running": "active",
            "Succeeded": "completed",
            "Failed": "failed",
            "Cancelled": "cancelled",
        }.get(status, "pending")

    @staticmethod
    def _plan_status(status: str) -> str:
        return {
            "Active": "active",
            "Ready": "active",
            "Superseded": "superseded",
            "Succeeded": "completed",
            "Failed": "failed",
        }.get(status, "active")

    @staticmethod
    def _action_status(status: str) -> str:
        return {
            "Pending": "pending",
            "Issued": "dispatched",
            "Accepted": "dispatched",
            "Running": "executing",
            "Succeeded": "completed",
            "Failed": "failed",
            "Timeout": "failed",
            "Rejected": "failed",
            "Stopped": "cancelled",
        }.get(status, "pending")
