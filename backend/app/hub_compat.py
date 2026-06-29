from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from .schemas import (
    ActionCreate,
    CommandCreate,
    CommandResponse,
    MessageRecord,
    SimulationRunCreate,
    SimulationTaskCreate,
    SnapshotCreate,
    TargetRegistryItemCreate,
    new_id,
    protocol_id,
    utc_now,
)


HUB_SOURCE = "hub-compat"


class HubRunCreate(BaseModel):
    run_id: str
    scene_id: str
    status: str = "running"
    phase: str = "observing"
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubSceneCreate(BaseModel):
    scene_name: str
    description: str | None = None
    map_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubEntityCreate(BaseModel):
    scene_id: str
    entity_name: str
    entity_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubObservationSubmit(BaseModel):
    message_id: str
    scene_id: str
    observation_type: str
    observed_at: str
    data: dict[str, Any] = Field(default_factory=dict)
    entity_id: str | None = None
    entity_updates: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "hub-compat"
    run_id: str | None = None
    task_id: str | None = None
    action_id: str | None = None
    command_id: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubExecutorResultSubmit(BaseModel):
    message_id: str
    scene_id: str
    success: bool
    result_code: str | None = None
    executed_at: str
    entity_id: str | None = None
    action_id: str | None = None
    command_id: str | None = None
    result_data: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubTaskCreate(BaseModel):
    run_id: str
    scene_id: str
    task_name: str
    task_type: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)
    final_review: dict[str, Any] | None = None
    status: str = "pending"
    source: str = "hub-compat"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class HubPlanCreate(BaseModel):
    task_id: str
    version: int = 1
    source: str = "hub-compat"
    status: str = "active"
    plan_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubActionCreate(BaseModel):
    task_id: str
    plan_id: str
    entity_id: str
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    postconditions: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubPlanStatusUpdate(BaseModel):
    run_id: str
    scene_id: str
    task_id: str
    plan_id: str
    action_id: str
    plan_status: str
    phase: str | None = None
    status: str | None = None
    source: str = "hub-compat"
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubActionStatusUpdate(BaseModel):
    run_id: str
    scene_id: str
    task_id: str
    plan_id: str
    action_id: str
    action_status: str
    phase: str | None = None
    status: str | None = None
    source: str = "hub-compat"
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubTraceCreate(BaseModel):
    trace_type: str = "shared"
    owner_module: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    scene_id: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubTraceEventCreate(BaseModel):
    event_type: str
    ref_id: str | None = None
    ref_type: str | None = None
    event_data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None


class HubSnapshotCreate(BaseModel):
    scene_id: str
    snapshot_type: str = "manual"
    label: str | None = None
    trace_id: str | None = None
    run_id: str | None = None


class HubMessageQuery(BaseModel):
    source: str | None = None
    envelope_type: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


def _hub_message_type(object_type: str) -> str:
    return f"hub.{object_type}"


def _object_id_key(object_type: str) -> str:
    return {
        "scene": "scene_id",
        "entity": "entity_id",
        "run": "run_id",
        "plan": "plan_id",
        "trace": "trace_id",
        "trace_event": "event_id",
        "snapshot": "snapshot_id",
        "executor_result": "executor_result_id",
        "plan_status": "status_event_id",
        "action_status": "status_event_id",
    }.get(object_type, "id")


def _store_hub_object(store: Any, object_type: str, obj: dict[str, Any]) -> dict[str, Any]:
    timestamp = utc_now()
    id_key = _object_id_key(object_type)
    object_id = str(obj.get(id_key) or obj.get("id") or new_id(object_type.upper()))
    obj[id_key] = object_id
    obj.setdefault("id", object_id)
    obj.setdefault("created_at", timestamp)
    obj["updated_at"] = timestamp
    obj["compatibility"] = {
        "enabled": True,
        "source": "scene_world_state_hub_phase_1",
        "storage": "message_record",
    }
    message = MessageRecord(
        messageId=f"HUB-{object_type.upper()}-{object_id}-{new_id('MSG')}",
        messageType=_hub_message_type(object_type),
        source=HUB_SOURCE,
        topic=f"hub/{object_type}",
        createdAt=timestamp,
        payload={
            "hubObjectType": object_type,
            "objectId": object_id,
            "object": obj,
        },
    )
    store.append_message(message)
    return obj


def _latest_hub_objects(store: Any, object_type: str, limit: int = 1000) -> list[dict[str, Any]]:
    records = store.query_messages(limit=limit, message_type=_hub_message_type(object_type), source=HUB_SOURCE)
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        obj = dict(record.payload.get("object") or {})
        object_id = str(record.payload.get("objectId") or obj.get(_object_id_key(object_type)) or obj.get("id") or "")
        if object_id and object_id not in by_id:
            by_id[object_id] = obj
    return list(by_id.values())


def find_hub_object(store: Any, object_type: str, object_id: str) -> dict[str, Any] | None:
    for obj in _latest_hub_objects(store, object_type):
        if object_id in {str(obj.get("id")), str(obj.get(_object_id_key(object_type)))}:
            return obj
    return None


def _scenario_to_scene(scenario: Any) -> dict[str, Any]:
    return {
        "id": scenario.scenarioId,
        "scene_id": scenario.scenarioId,
        "scene_name": scenario.name,
        "description": "platform scenario compatibility view",
        "map_config": scenario.map.model_dump(by_alias=True) if hasattr(scenario.map, "model_dump") else scenario.map,
        "metadata": {
            "siteMapId": scenario.siteMapId,
            "siteMapVersion": scenario.siteMapVersion,
            "robotCodes": scenario.robotCodes,
            "source": "platform.scenario",
        },
        "compatibility": {"enabled": True, "storage": "platform_scenario"},
    }


def _target_to_entity(target: Any) -> dict[str, Any]:
    return {
        "id": target.targetId,
        "entity_id": target.targetId,
        "scene_id": target.mapId,
        "entity_name": target.displayName,
        "entity_type": target.targetType,
        "properties": {
            "pose": target.pose,
            "geometryRef": target.geometryRef,
            **(target.metadata or {}),
        },
        "metadata": {"status": target.status, "version": target.version, "source": "target_registry"},
        "compatibility": {"enabled": True, "storage": "target_registry"},
    }


def _robot_to_entity(robot: Any) -> dict[str, Any]:
    return {
        "id": robot.robotId,
        "entity_id": robot.robotId,
        "scene_id": "default-site-a",
        "entity_name": robot.robotId,
        "entity_type": "robot",
        "properties": robot.model_dump(),
        "metadata": {"source": "robot_state"},
        "compatibility": {"enabled": True, "storage": "robot_state"},
    }


def _run_to_hub(run: Any, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    overlay = overlay or {}
    return {
        "id": run.runId,
        "run_id": run.runId,
        "scene_id": overlay.get("scene_id") or run.scenarioId,
        "status": overlay.get("status") or str(run.status).lower(),
        "phase": overlay.get("phase") or ("executing" if run.status == "Running" else "observing"),
        "metadata": overlay.get("metadata") or {},
        "active_task_id": None,
        "active_plan_id": None,
        "active_action_id": None,
        "current_state_version": None,
        "started_at": run.startedAt,
        "finished_at": run.finishedAt,
        "created_at": run.createdAt,
        "updated_at": run.updatedAt,
        "platform": run.model_dump(),
    }


def task_to_hub_response(task: Any) -> dict[str, Any]:
    return {
        **task.model_dump(),
        "id": task.taskId,
        "task_id": task.taskId,
        "run_id": task.runId,
        "scene_id": task.input.get("scene_id") or task.input.get("sceneId") or "default-site-a",
        "task_name": task.goal,
        "task_type": task.input.get("task_type"),
        "definition": task.input,
        "final_review": task.input.get("final_review"),
        "source": task.createdBy,
        "metadata": task.constraints,
    }


def _plan_to_hub(plan: Any) -> dict[str, Any]:
    if isinstance(plan, dict):
        return plan
    return {
        **plan.model_dump(),
        "id": plan.planId,
        "plan_id": plan.planId,
        "task_id": plan.taskId,
        "version": plan.planVersion,
        "source": plan.generatedBy,
        "status": str(plan.status).lower(),
        "plan_data": {"steps": [step.model_dump() for step in plan.steps]},
        "metadata": {
            "strategy": plan.strategy,
            "dependencies": plan.dependencies,
            "assumptions": plan.assumptions,
        },
    }


def _action_command(action_type: str) -> str:
    command = {"move": "goto_pose"}.get(action_type, action_type)
    if command == "where":
        raise ValueError("where is a query command and is not accepted as Hub Action")
    allowed = {"goto_pose", "stop", "pick", "place", "load", "unload", "inspect", "charge", "wait"}
    if command not in allowed:
        raise ValueError(f"unsupported Hub action_type: {action_type}")
    return command


def _normalize_action_parameters(action_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
    params = dict(parameters or {})
    if action_type == "move":
        target_pose = params.pop("target_pose", None) or params.pop("pose", None)
        if isinstance(target_pose, list) and len(target_pose) >= 2:
            params.setdefault("x", target_pose[0])
            params.setdefault("y", target_pose[1])
            params.setdefault("z", target_pose[2] if len(target_pose) >= 3 else 0)
            params.setdefault("yaw", target_pose[3] if len(target_pose) >= 4 else 0)
    return params


def _robot_code_for_entity(store: Any, entity_id: str) -> str:
    robot_ids = [robot.robotId for robot in store.robots()]
    if entity_id in robot_ids:
        return entity_id
    entity = find_hub_object(store, "entity", entity_id)
    if entity:
        properties = entity.get("properties") or {}
        for key in ("robotCode", "robot_code", "robotId", "robot_id"):
            if properties.get(key):
                return str(properties[key])
        entity_name = str(entity.get("entity_name") or "")
        if entity_name in robot_ids:
            return entity_name
    return robot_ids[0] if robot_ids else "robot-001"


def action_to_hub_response(action: Any, request: HubActionCreate | None = None, hub_status: str | None = None) -> dict[str, Any]:
    action_type = request.action_type if request else action.command
    return {
        **action.model_dump(),
        "id": action.actionId,
        "action_id": action.actionId,
        "task_id": action.taskId,
        "plan_id": action.planId,
        "entity_id": request.entity_id if request else action.robotCode,
        "action_type": action_type,
        "parameters": action.params,
        "preconditions": request.preconditions if request else {},
        "postconditions": request.postconditions if request else {},
        "hub_status": hub_status or str(action.status).lower(),
        "metadata": request.metadata if request else {"source": "platform_action"},
    }


def _find_plan(store: Any, plan_id: str) -> dict[str, Any] | None:
    for run in store.list_simulation_runs(limit=1000):
        for task in store.list_run_tasks(run.runId):
            for plan in store.list_task_plans(task.taskId):
                if plan.planId == plan_id:
                    return _plan_to_hub(plan)
    return find_hub_object(store, "plan", plan_id)


def create_hub_action(
    store: Any,
    payload: dict[str, Any],
    issue_command: Callable[[CommandCreate], CommandResponse],
) -> dict[str, Any]:
    request = HubActionCreate.model_validate(payload)
    task = store.get_task(request.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    plan = _find_plan(store, request.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    if str(plan.get("task_id")) != request.task_id:
        raise HTTPException(status_code=400, detail="plan does not belong to task")

    command = _action_command(request.action_type)
    params = _normalize_action_parameters(request.action_type, request.parameters)
    robot_code = _robot_code_for_entity(store, request.entity_id)
    if hasattr(store, "validate_robot_path_group"):
        try:
            store.validate_robot_path_group(robot_code, params.get("pathGroupId"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        action = store.create_action(
            ActionCreate(
                runId=task.runId,
                taskId=request.task_id,
                planId=request.plan_id,
                robotCode=robot_code,
                command=command,  # type: ignore[arg-type]
                params=params,
                operatorId=str(request.metadata.get("operatorId") or "hub-compat"),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if action is None:
        raise HTTPException(status_code=404, detail="simulation run or task not found")

    command_response = issue_command(
        CommandCreate(
            robotCode=action.robotCode,
            command=action.command,
            params=action.params,
            timeoutMs=action.timeoutMs,
            issuedBy="hub-compat",
            operatorId=str(request.metadata.get("operatorId") or "hub-compat"),
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
    return action_to_hub_response(issued, request=request)


def _scene_id_to_scenario_id(store: Any, scene_id: str) -> str:
    scenarios = store.list_scenarios()
    for scenario in scenarios:
        if scene_id in {scenario.scenarioId, scenario.siteMapId}:
            return scenario.scenarioId
    return scenarios[0].scenarioId if scenarios else "default-site-a"


def _latest_run_for_scene(store: Any, scene_id: str | None) -> Any | None:
    runs = store.list_simulation_runs(limit=1000)
    if scene_id is None:
        return runs[0] if runs else None
    for run in runs:
        overlay = find_hub_object(store, "run", run.runId) or {}
        if scene_id in {run.scenarioId, run.mapId, overlay.get("scene_id")}:
            return run
    return None


def register_hub_compat_routes(app: Any, store_provider: Any) -> None:
    router = APIRouter(tags=["Hub Compatibility"])

    def current_store() -> Any:
        return store_provider() if callable(store_provider) else store_provider

    @router.get("/health")
    def root_health() -> dict[str, Any]:
        store = current_store()
        storage = store.storage_health()
        return {
            "status": "ok" if storage.get("status") == "ok" else storage.get("status", "unknown"),
            "service": "scene-world-state-hub-compatible",
            "compatibility": True,
            "storage": storage,
            "time": utc_now(),
        }

    @router.post("/api/v1/runs")
    def create_hub_run(request: HubRunCreate) -> dict[str, Any]:
        store = current_store()
        scenario_id = _scene_id_to_scenario_id(store, request.scene_id)
        try:
            run = store.create_simulation_run(
                SimulationRunCreate(runId=request.run_id, scenarioId=scenario_id, name=f"Hub run {request.run_id}")
            )
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=409 if "already exists" in detail else 404, detail=detail) from exc
        overlay = _store_hub_object(
            store,
            "run",
            {
                "run_id": run.runId,
                "scene_id": request.scene_id,
                "status": request.status,
                "phase": request.phase,
                "metadata": request.metadata,
            },
        )
        return _run_to_hub(run, overlay=overlay)

    @router.get("/api/v1/runs/{run_id}")
    def get_hub_run(run_id: str) -> dict[str, Any]:
        store = current_store()
        run = store.get_simulation_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _run_to_hub(run, overlay=find_hub_object(store, "run", run_id))

    @router.post("/api/v1/scenes")
    def create_hub_scene(request: HubSceneCreate) -> dict[str, Any]:
        store = current_store()
        existing_names = {scene["scene_name"] for scene in _latest_hub_objects(store, "scene")}
        existing_names.update(scenario.name for scenario in store.list_scenarios())
        if request.scene_name in existing_names:
            raise HTTPException(status_code=409, detail="scene_name already exists")
        return _store_hub_object(
            store,
            "scene",
            {
                "scene_id": new_id("SCENE"),
                "scene_name": request.scene_name,
                "description": request.description,
                "map_config": request.map_config,
                "metadata": request.metadata,
            },
        )

    @router.get("/api/v1/scenes")
    def list_hub_scenes() -> list[dict[str, Any]]:
        store = current_store()
        scenes = [_scenario_to_scene(scenario) for scenario in store.list_scenarios()]
        scenes.extend(_latest_hub_objects(store, "scene"))
        return scenes

    @router.get("/api/v1/scenes/{scene_id}")
    def get_hub_scene(scene_id: str) -> dict[str, Any]:
        store = current_store()
        for scenario in store.list_scenarios():
            if scene_id in {scenario.scenarioId, scenario.siteMapId}:
                return _scenario_to_scene(scenario)
        scene = find_hub_object(store, "scene", scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="scene not found")
        return scene

    @router.post("/api/v1/entities")
    def create_hub_entity(request: HubEntityCreate) -> dict[str, Any]:
        store = current_store()
        for entity in _latest_hub_objects(store, "entity"):
            if entity.get("scene_id") == request.scene_id and entity.get("entity_name") == request.entity_name:
                raise HTTPException(status_code=409, detail="entity_name already exists in scene")
        return _store_hub_object(
            store,
            "entity",
            {
                "entity_id": new_id("ENTITY"),
                "scene_id": request.scene_id,
                "entity_name": request.entity_name,
                "entity_type": request.entity_type,
                "properties": request.properties,
                "metadata": request.metadata,
            },
        )

    @router.get("/api/v1/entities")
    def list_hub_entities(scene_id: str, entity_type: str | None = None) -> list[dict[str, Any]]:
        store = current_store()
        entities = [_target_to_entity(target) for target in store.list_targets(status=None)]
        entities.extend(_robot_to_entity(robot) for robot in store.robots())
        entities.extend(_latest_hub_objects(store, "entity"))
        return [
            entity
            for entity in entities
            if entity.get("scene_id") == scene_id and (entity_type is None or entity.get("entity_type") == entity_type)
        ]

    @router.get("/api/v1/entities/{entity_id}")
    def get_hub_entity(entity_id: str) -> dict[str, Any]:
        store = current_store()
        target = store.get_target(entity_id)
        if target:
            return _target_to_entity(target)
        for robot in store.robots():
            if robot.robotId == entity_id:
                return _robot_to_entity(robot)
        entity = find_hub_object(store, "entity", entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        return entity

    @router.post("/api/v1/observations")
    def submit_hub_observation(request: HubObservationSubmit) -> dict[str, Any]:
        store = current_store()
        payload = {
            "schemaVersion": "1.0",
            "messageType": "event",
            "event": request.observation_type,
            "eventId": request.event_id or protocol_id("EVT"),
            "runId": request.run_id,
            "sceneId": request.scene_id,
            "taskId": request.task_id,
            "actionId": request.action_id,
            "commandId": request.command_id,
            "requestId": request.request_id,
            "robotCode": request.entity_id,
            "traceId": request.trace_id or request.run_id,
            "source": request.source,
            "timestamp": request.observed_at,
            "data": {
                **request.data,
                "entityId": request.entity_id,
                "entityUpdates": request.entity_updates,
                "metadata": request.metadata,
            },
            "error": None,
        }
        message = MessageRecord(
            messageId=request.message_id,
            messageType="event",
            source=request.source,
            topic=f"hub/observations/{request.scene_id}",
            createdAt=utc_now(),
            payload=payload,
        )
        store.append_message(message)
        applied = store.ingest_observation_from_message(message) if hasattr(store, "ingest_observation_from_message") else None
        return {
            "id": applied.observationId if applied else request.message_id,
            "observation_id": applied.observationId if applied else request.message_id,
            "message_id": request.message_id,
            "scene_id": request.scene_id,
            "entity_id": request.entity_id,
            "observation_type": request.observation_type,
            "observed_at": request.observed_at,
            "data": request.data,
            "entity_updates": request.entity_updates,
            "processed": applied is not None,
            "platform_observation": applied.model_dump() if applied else None,
        }

    @router.get("/api/v1/current-state")
    def get_hub_current_state(
        scene_id: str,
        run_id: str | None = None,
        entity_id: str | None = None,
        state_type: str | None = None,
    ) -> dict[str, Any]:
        store = current_store()
        run = store.get_simulation_run(run_id) if run_id else _latest_run_for_scene(store, scene_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found for scene")
        state = store.get_current_state(run.runId)
        if state is None:
            raise HTTPException(status_code=404, detail="current state not found")
        entities = []
        for robot in state.robotStates:
            robot_id = str(robot.get("robotId") or robot.get("robotCode") or "")
            if entity_id and robot_id != entity_id:
                continue
            if state_type and state_type not in {"robot", "position", "status"}:
                continue
            entities.append(
                {
                    "entity_id": robot_id,
                    "state_type": "robot",
                    "state_data": robot,
                    "last_observation_id": state.lastObservationId,
                    "last_observed_at": state.lastObservationAt,
                    "version": state.stateVersion,
                }
            )
        return {
            "id": state.runId,
            "scene_id": scene_id,
            "run_id": state.runId,
            "state_version": state.stateVersion,
            "active_task_id": state.taskState.get("activeTaskId"),
            "active_plan_id": (state.activePlan or {}).get("planId") if state.activePlan else None,
            "active_action_id": state.taskState.get("activeActionId"),
            "last_observation_id": state.lastObservationId,
            "last_observation_at": state.lastObservationAt,
            "entities": entities,
            "platform": state.model_dump(),
        }

    @router.post("/api/v1/executor-results")
    def submit_hub_executor_result(request: HubExecutorResultSubmit) -> dict[str, Any]:
        store = current_store()
        result_id = new_id("EXEC-RESULT")
        obj = {
            "executor_result_id": result_id,
            "message_id": request.message_id,
            "scene_id": request.scene_id,
            "success": request.success,
            "result_code": request.result_code,
            "executed_at": request.executed_at,
            "entity_id": request.entity_id,
            "action_id": request.action_id,
            "command_id": request.command_id,
            "result_data": request.result_data,
            "error_message": request.error_message,
            "metadata": request.metadata,
        }
        stored = _store_hub_object(store, "executor_result", obj)
        message = MessageRecord(
            messageId=request.message_id,
            messageType="executor_result",
            source=HUB_SOURCE,
            topic=f"hub/executor-results/{request.scene_id}",
            createdAt=utc_now(),
            payload={
                "runId": request.run_id,
                "taskId": request.task_id,
                "actionId": request.action_id,
                "commandId": request.command_id,
                "traceId": request.trace_id,
                "requestId": request.request_id,
                "result": stored,
            },
        )
        store.append_message(message)
        return stored

    @router.get("/api/v1/executor-results")
    def list_hub_executor_results(
        entity_id: str | None = None,
        command_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        store = current_store()
        results = _latest_hub_objects(store, "executor_result", limit=limit)
        return [
            result
            for result in results
            if (entity_id is None or result.get("entity_id") == entity_id)
            and (command_id is None or result.get("command_id") == command_id)
        ][:limit]

    @router.post("/api/v1/tasks")
    def create_hub_task(request: HubTaskCreate) -> dict[str, Any]:
        store = current_store()
        task = store.create_simulation_task(
            request.run_id,
            SimulationTaskCreate(
                goal=request.task_name,
                input={
                    **request.definition,
                    "scene_id": request.scene_id,
                    "task_type": request.task_type,
                    "final_review": request.final_review,
                    "hub_status": request.status,
                },
                constraints=request.metadata,
                expectedOutcome=str(request.final_review) if request.final_review else None,
                createdBy=request.source,
            ),
        )
        if task is None:
            raise HTTPException(status_code=404, detail="run not found")
        return task_to_hub_response(task)

    @router.post("/api/v1/plans")
    def create_hub_plan(request: HubPlanCreate) -> dict[str, Any]:
        store = current_store()
        task = store.get_task(request.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        existing_versions = {plan.planVersion for plan in store.list_task_plans(request.task_id)}
        existing_versions.update(
            int(plan.get("version"))
            for plan in _latest_hub_objects(store, "plan")
            if plan.get("task_id") == request.task_id and plan.get("version") is not None
        )
        if request.version in existing_versions:
            raise HTTPException(status_code=409, detail="plan version already exists")
        return _store_hub_object(
            store,
            "plan",
            {
                "plan_id": protocol_id("PLAN"),
                "task_id": request.task_id,
                "version": request.version,
                "source": request.source,
                "status": request.status,
                "plan_data": request.plan_data,
                "metadata": request.metadata,
            },
        )

    @router.get("/api/v1/plans/{plan_id}")
    def get_hub_plan(plan_id: str) -> dict[str, Any]:
        store = current_store()
        plan = _find_plan(store, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="plan not found")
        return plan

    @router.post("/api/v1/plans/{plan_id}/status")
    def update_hub_plan_status(plan_id: str, request: HubPlanStatusUpdate) -> dict[str, Any]:
        store = current_store()
        if plan_id != request.plan_id:
            raise HTTPException(status_code=400, detail="path plan_id does not match body plan_id")
        plan = _find_plan(store, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="plan not found")
        event = _store_hub_object(
            store,
            "plan_status",
            {
                "status_event_id": protocol_id("PLAN-STATUS"),
                **request.model_dump(),
            },
        )
        return {"plan": {**plan, "status": request.plan_status}, "status_event": event}

    @router.post("/api/v1/actions/{action_id}/status")
    def update_hub_action_status(action_id: str, request: HubActionStatusUpdate) -> dict[str, Any]:
        store = current_store()
        if action_id != request.action_id:
            raise HTTPException(status_code=400, detail="path action_id does not match body action_id")
        action = store.get_action(action_id)
        if action is None:
            raise HTTPException(status_code=404, detail="action not found")
        event = _store_hub_object(
            store,
            "action_status",
            {
                "status_event_id": protocol_id("ACTION-STATUS"),
                **request.model_dump(),
            },
        )
        return {"action": action_to_hub_response(action, hub_status=request.action_status), "status_event": event}

    @router.post("/api/v1/traces")
    def create_hub_trace(request: HubTraceCreate) -> dict[str, Any]:
        store = current_store()
        return _store_hub_object(
            store,
            "trace",
            {
                "trace_id": protocol_id("TRACE"),
                "trace_type": request.trace_type,
                "owner_module": request.owner_module,
                "run_id": request.run_id,
                "task_id": request.task_id,
                "scene_id": request.scene_id,
                "summary": request.summary,
                "metadata": request.metadata,
            },
        )

    @router.post("/api/v1/traces/{trace_id}/events")
    def append_hub_trace_event(trace_id: str, request: HubTraceEventCreate) -> dict[str, Any]:
        store = current_store()
        if store.get_trace(trace_id).status == "NotFound" and find_hub_object(store, "trace", trace_id) is None:
            raise HTTPException(status_code=404, detail="trace not found")
        event_count = len([event for event in _latest_hub_objects(store, "trace_event") if event.get("trace_id") == trace_id])
        return _store_hub_object(
            store,
            "trace_event",
            {
                "event_id": protocol_id("TRACE-EVENT"),
                "trace_id": trace_id,
                "event_type": request.event_type,
                "ref_id": request.ref_id,
                "ref_type": request.ref_type,
                "event_data": request.event_data,
                "timestamp": request.timestamp or utc_now(),
                "seq": event_count + 1,
            },
        )

    @router.get("/api/v1/traces/{trace_id}/events")
    def list_hub_trace_events(trace_id: str) -> list[dict[str, Any]]:
        store = current_store()
        return sorted(
            [event for event in _latest_hub_objects(store, "trace_event") if event.get("trace_id") == trace_id],
            key=lambda event: int(event.get("seq") or 0),
        )

    @router.post("/api/v1/snapshots")
    def create_hub_snapshot(request: HubSnapshotCreate) -> dict[str, Any]:
        store = current_store()
        snapshot_id = protocol_id("SNAP")
        state_dump: dict[str, Any] = {}
        if request.run_id:
            state = store.get_current_state(request.run_id)
            state_dump = state.model_dump() if state else {}
        return _store_hub_object(
            store,
            "snapshot",
            {
                "snapshot_id": snapshot_id,
                "scene_id": request.scene_id,
                "snapshot_type": request.snapshot_type,
                "label": request.label,
                "state_dump": state_dump,
                "trace_id": request.trace_id,
                "run_id": request.run_id,
            },
        )

    @router.get("/api/v1/snapshots/{snapshot_id}")
    def get_hub_snapshot(snapshot_id: str) -> dict[str, Any]:
        store = current_store()
        snapshot = find_hub_object(store, "snapshot", snapshot_id)
        if snapshot is None:
            for run in store.list_simulation_runs(limit=1000):
                for item in store.list_snapshots(run.runId):
                    if item.snapshotId == snapshot_id:
                        return {
                            **item.model_dump(),
                            "id": item.snapshotId,
                            "snapshot_id": item.snapshotId,
                            "scene_id": run.scenarioId,
                            "snapshot_type": item.reason,
                            "label": item.reason,
                            "state_dump": item.snapshot,
                            "trace_id": item.traceId,
                        }
            raise HTTPException(status_code=404, detail="snapshot not found")
        return snapshot

    @router.get("/api/v1/snapshots")
    def list_hub_snapshots(scene_id: str | None = None, trace_id: str | None = None) -> list[dict[str, Any]]:
        store = current_store()
        snapshots = _latest_hub_objects(store, "snapshot")
        return [
            snapshot
            for snapshot in snapshots
            if (scene_id is None or snapshot.get("scene_id") == scene_id)
            and (trace_id is None or snapshot.get("trace_id") == trace_id)
        ]

    @router.post("/api/v1/messages/query")
    def query_hub_messages(request: HubMessageQuery) -> dict[str, Any]:
        store = current_store()
        messages = store.query_messages(
            limit=request.limit,
            message_type=request.envelope_type,
            task_id=request.task_id,
            trace_id=request.trace_id,
            source=request.source,
            created_from=request.since,
            created_to=request.until,
        )
        return {
            "items": [message.model_dump() for message in messages],
            "count": len(messages),
            "limit": request.limit,
            "compatibility": True,
        }

    app.include_router(router)
