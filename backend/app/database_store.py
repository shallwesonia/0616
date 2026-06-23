from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select

from .database import Database, workspace_id_from_env
from .db_models import (
    AuditLogRecord,
    Base,
    ExportJobRecord,
    MapDraftRecord,
    MessageRecordRow,
    RobotInstanceRecord,
    SimulationActionRecord,
    SimulationCurrentStateRecord,
    SimulationObservationRecord,
    SimulationPlanRecord,
    SimulationPlanStepRecord,
    SimulationRunRecord,
    SimulationSnapshotRecord,
    SimulationTaskRecord,
    SimulationTraceHeaderRecord,
    SimulationTraceSpanRecord,
    SiteMapRecord,
    now_utc,
)
from .runtime_cache import RuntimeCache
from .schemas import (
    ActionCreate,
    CurrentState,
    MessageRecord,
    Observation,
    PlanStep,
    RobotState,
    ScenarioSummary,
    SimulationAction,
    SimulationEventCreate,
    SimulationPlan,
    SimulationRun,
    SimulationRunCreate,
    SimulationTask,
    SimulationTaskCreate,
    Snapshot,
    SnapshotCreate,
    TaskFromTemplateCreate,
    TaskTemplate,
    TraceResponse,
    TraceSpan,
    SiteMap,
    new_id,
    protocol_id,
    utc_now,
)
from .store import DATA_DIR, DEFAULT_MAP, EXPORT_DIR, STATE_PATH, JsonStore, default_state


def parse_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return now_utc()


def iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class DatabaseStore(JsonStore):
    TASK_TEMPLATES = [
        TaskTemplate(
            templateId="sort-transfer",
            name="Sorting transfer",
            description="Move the robot from the current position to a sorting station.",
            defaultGoal="Move material to the sorting station",
            defaultInput={"command": "goto_pose", "target": {"x": 760, "y": 420, "z": 0, "yaw": 0}},
            supportedCommands=["goto_pose", "where", "stop"],
        ),
        TaskTemplate(
            templateId="pick-and-place",
            name="Pick and place",
            description="Simulate pick, carry and place as a single goto_pose execution step.",
            defaultGoal="Pick material and place it at the target station",
            defaultInput={"command": "goto_pose", "target": {"x": 220, "y": 240, "z": 0, "yaw": 0}},
            supportedCommands=["goto_pose", "where", "stop"],
        ),
        TaskTemplate(
            templateId="inspect-point",
            name="Point inspection",
            description="Move to a configured point and report current pose.",
            defaultGoal="Inspect the configured point and report pose",
            defaultInput={"command": "where", "target": {}},
            supportedCommands=["where", "goto_pose"],
        ),
    ]
    ACTION_TERMINAL_STATUSES = {"Succeeded", "Failed", "Rejected", "Stopped", "Timeout"}
    TASK_TERMINAL_STATUSES = {"Succeeded", "Failed", "Cancelled"}

    def __init__(
        self,
        database_url: str,
        workspace_id: UUID | None = None,
        state_path: Path = STATE_PATH,
        create_schema: bool = False,
        runtime_cache: RuntimeCache | None = None,
    ) -> None:
        self.database = Database(database_url)
        self.workspace_id = workspace_id or workspace_id_from_env()
        self.state_path = state_path
        self.runtime_cache = runtime_cache or RuntimeCache(None, self.workspace_id, enabled=False)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        if create_schema:
            Base.metadata.create_all(self.database.engine)
        self.seed_if_empty()
        self.runtime_cache.warm(self.robots(), self.messages(limit=self.runtime_cache.message_limit))

    def seed_if_empty(self) -> None:
        with self.database.session() as session:
            existing = session.scalar(
                select(func.count()).select_from(SiteMapRecord).where(
                    SiteMapRecord.workspace_id == self.workspace_id
                )
            )
            if existing:
                return

            state = self._seed_state()
            map_data = state.get("map") or DEFAULT_MAP
            session.add(self._site_map_row(map_data, status="active"))

            for draft in state.get("drafts", {}).values():
                draft_map = draft.get("map") or {}
                session.add(
                    MapDraftRecord(
                        workspace_id=self.workspace_id,
                        draft_id=draft.get("draftId") or new_id("draft"),
                        map_id=draft_map.get("id", "site-a"),
                        status=draft.get("status", "editing"),
                        map_json=draft_map,
                        created_at=parse_datetime(draft.get("createdAt")),
                        published_at=parse_datetime(draft.get("publishedAt")) if draft.get("publishedAt") else None,
                    )
                )

            for robot in state.get("robots", []):
                session.add(self._robot_row(RobotState.model_validate(robot)))

            seen_message_ids: set[str] = set()
            for message_data in state.get("messages", []):
                message = MessageRecord.model_validate(message_data)
                if message.messageId in seen_message_ids:
                    continue
                seen_message_ids.add(message.messageId)
                session.add(self._message_row(message))

            for audit in state.get("auditLogs", []):
                session.add(
                    AuditLogRecord(
                        workspace_id=self.workspace_id,
                        audit_id=audit.get("id") or new_id("audit"),
                        actor_id=audit.get("actorId", "system"),
                        actor_type=audit.get("actorType", "service"),
                        action=audit.get("action", "legacy.import"),
                        resource_type=audit.get("resourceType", "legacy"),
                        resource_id=audit.get("resourceId", "unknown"),
                        before_json=audit.get("before"),
                        after_json=audit.get("after"),
                        created_at=parse_datetime(audit.get("createdAt")),
                    )
                )

            self._add_audit(
                session,
                "storage.seed.completed",
                "workspace",
                str(self.workspace_id),
                after={"source": str(self.state_path) if self.state_path.exists() else "default_state"},
            )

    def _seed_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            with self.state_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        return default_state()

    def current_map(self) -> SiteMap:
        with self.database.session() as session:
            row = session.scalar(
                select(SiteMapRecord)
                .where(
                    SiteMapRecord.workspace_id == self.workspace_id,
                    SiteMapRecord.status == "active",
                )
                .order_by(SiteMapRecord.updated_at.desc())
                .limit(1)
            )
            if row is None:
                raise RuntimeError("active map not found")
            return SiteMap.model_validate(row.map_json)

    def save_draft(self, map_data: SiteMap) -> str:
        draft_id = new_id("draft")
        with self.database.session() as session:
            session.add(
                MapDraftRecord(
                    workspace_id=self.workspace_id,
                    draft_id=draft_id,
                    map_id=map_data.id,
                    status="editing",
                    map_json=map_data.model_dump(by_alias=True),
                    created_at=now_utc(),
                )
            )
            self._add_audit(
                session,
                "map.draft.created",
                "map",
                map_data.id,
                after={"draftId": draft_id, "configVersion": map_data.configVersion},
            )
        return draft_id

    def draft_map(self, draft_id: str) -> SiteMap | None:
        with self.database.session() as session:
            row = session.scalar(
                select(MapDraftRecord).where(
                    MapDraftRecord.workspace_id == self.workspace_id,
                    MapDraftRecord.draft_id == draft_id,
                )
            )
            return SiteMap.model_validate(row.map_json) if row else None

    def publish_draft(self, draft_id: str) -> SiteMap | None:
        with self.database.session() as session:
            draft = session.scalar(
                select(MapDraftRecord).where(
                    MapDraftRecord.workspace_id == self.workspace_id,
                    MapDraftRecord.draft_id == draft_id,
                )
            )
            if draft is None:
                return None

            map_json = dict(draft.map_json)
            map_json["configVersion"] = f"v{utc_now().replace(':', '').replace('-', '')[:15]}"
            active_maps = session.scalars(
                select(SiteMapRecord).where(
                    SiteMapRecord.workspace_id == self.workspace_id,
                    SiteMapRecord.map_id == draft.map_id,
                    SiteMapRecord.status == "active",
                )
            ).all()
            for active_map in active_maps:
                active_map.status = "archived"
                active_map.updated_at = now_utc()

            session.add(self._site_map_row(map_json, status="active"))
            draft.status = "published"
            draft.published_at = now_utc()
            self._add_audit(
                session,
                "map.draft.published",
                "map",
                draft.map_id,
                after={"draftId": draft_id, "configVersion": map_json["configVersion"]},
            )
            return SiteMap.model_validate(map_json)

    def storage_health(self) -> dict[str, Any]:
        health = self.database.health()
        if health["status"] != "ok":
            return health
        with self.database.session() as session:
            health.update(
                {
                    "workspaceId": str(self.workspace_id),
                    "messageCount": session.scalar(
                        select(func.count()).select_from(MessageRecordRow).where(
                            MessageRecordRow.workspace_id == self.workspace_id
                        )
                    ),
                    "robotCount": session.scalar(
                        select(func.count()).select_from(RobotInstanceRecord).where(
                            RobotInstanceRecord.workspace_id == self.workspace_id
                        )
                    ),
                    "draftCount": session.scalar(
                        select(func.count()).select_from(MapDraftRecord).where(
                            MapDraftRecord.workspace_id == self.workspace_id
                        )
                    ),
                }
            )
        return health

    def runtime_cache_health(self) -> dict[str, Any]:
        return self.runtime_cache.health()

    def runtime_robots(self) -> list[RobotState]:
        cached = self.runtime_cache.robots()
        return cached if cached is not None else self.robots()

    def runtime_messages(self, limit: int = 100) -> list[MessageRecord]:
        cached = self.runtime_cache.messages(limit)
        return cached if cached is not None else self.messages(limit)

    def last_heartbeat_at(self) -> str | None:
        for message in self.messages(limit=200):
            if message.messageType == "heartbeat" or message.payload.get("event") == "pose.updated":
                return message.createdAt
        return None

    def robots(self) -> list[RobotState]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RobotInstanceRecord)
                .where(RobotInstanceRecord.workspace_id == self.workspace_id)
                .order_by(RobotInstanceRecord.robot_code)
            ).all()
            return [self._robot_model(row) for row in rows]

    def messages(self, limit: int = 100) -> list[MessageRecord]:
        with self.database.session() as session:
            rows = session.scalars(
                select(MessageRecordRow)
                .where(MessageRecordRow.workspace_id == self.workspace_id)
                .order_by(MessageRecordRow.created_at.desc())
                .limit(limit)
            ).all()
            return [self._message_model(row) for row in rows]

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
        query = select(MessageRecordRow).where(MessageRecordRow.workspace_id == self.workspace_id)
        if message_type:
            query = query.where(MessageRecordRow.message_type == message_type)
        if robot_code:
            query = query.where(MessageRecordRow.robot_code == robot_code)
        if command_id:
            query = query.where(MessageRecordRow.command_id == command_id)
        if trace_id:
            query = query.where(MessageRecordRow.trace_id == trace_id)
        if task_id:
            query = query.where(MessageRecordRow.task_id == task_id)
        if request_id:
            query = query.where(MessageRecordRow.request_id == request_id)
        if event:
            query = query.where(MessageRecordRow.event == event)
        if topic:
            query = query.where(MessageRecordRow.topic.contains(topic))
        if source:
            query = query.where(MessageRecordRow.source == source)
        if created_from:
            query = query.where(MessageRecordRow.created_at >= parse_datetime(created_from))
        if created_to:
            query = query.where(MessageRecordRow.created_at <= parse_datetime(created_to))

        with self.database.session() as session:
            rows = session.scalars(query.order_by(MessageRecordRow.created_at.desc()).limit(limit)).all()
            return [self._message_model(row) for row in rows]

    def command_trace(self, command_id: str) -> list[MessageRecord]:
        with self.database.session() as session:
            rows = session.scalars(
                select(MessageRecordRow)
                .where(
                    MessageRecordRow.workspace_id == self.workspace_id,
                    or_(
                        MessageRecordRow.command_id == command_id,
                        MessageRecordRow.trace_id == command_id,
                        MessageRecordRow.task_id == command_id,
                        MessageRecordRow.request_id == command_id,
                    ),
                )
                .order_by(MessageRecordRow.created_at.asc())
            ).all()
            return [self._message_model(row) for row in rows]

    def recent_runtime_summary(self) -> dict[str, Any]:
        latest_command = self.query_messages(limit=1, message_type="command")
        latest_event = self.query_messages(limit=1, message_type="event")
        messages = [message.model_dump() for message in self.messages(limit=200)]
        return {
            "lastCommand": latest_command[0].model_dump() if latest_command else None,
            "lastEvent": latest_event[0].model_dump() if latest_event else None,
            "lastError": self._last_error_message(list(reversed(messages))),
        }

    def append_message(self, message: MessageRecord) -> None:
        inserted = False
        with self.database.session() as session:
            existing = session.scalar(
                select(MessageRecordRow.id).where(
                    MessageRecordRow.workspace_id == self.workspace_id,
                    MessageRecordRow.message_id == message.messageId,
                )
            )
            if existing is None:
                session.add(self._message_row(message))
                inserted = True
        if inserted:
            self.runtime_cache.append_message(message)

    def upsert_robot_state(self, robot: RobotState) -> None:
        with self.database.session() as session:
            row = session.scalar(
                select(RobotInstanceRecord).where(
                    RobotInstanceRecord.workspace_id == self.workspace_id,
                    RobotInstanceRecord.robot_code == robot.robotId,
                )
            )
            if row is None:
                session.add(self._robot_row(robot))
            else:
                row.robot_type = robot.robotType
                row.robot_state = robot.state
                row.x = robot.x
                row.y = robot.y
                row.progress = robot.progress
                row.current_action = robot.currentAction
                row.updated_at = parse_datetime(robot.updatedAt)
        self.runtime_cache.set_robot(robot)

    def create_export(self, export_type: str) -> tuple[str, str]:
        export_id = new_id("export")
        file_name = f"{export_id}-{export_type}.json"
        payload = {
            "exportId": export_id,
            "exportType": export_type,
            "workspaceId": str(self.workspace_id),
            "createdAt": utc_now(),
            "data": self.export_state(),
        }
        file_path = EXPORT_DIR / file_name
        with file_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        with self.database.session() as session:
            session.add(
                ExportJobRecord(
                    workspace_id=self.workspace_id,
                    export_id=export_id,
                    export_type=export_type,
                    export_status="completed",
                    file_name=file_name,
                    created_at=now_utc(),
                    completed_at=now_utc(),
                )
            )
            self._add_audit(
                session,
                "export.created",
                "export",
                export_id,
                after={"exportType": export_type, "fileName": file_name},
            )
        return export_id, file_name

    def export_state(self) -> dict[str, Any]:
        with self.database.session() as session:
            drafts = session.scalars(
                select(MapDraftRecord).where(MapDraftRecord.workspace_id == self.workspace_id)
            ).all()
            audits = session.scalars(
                select(AuditLogRecord)
                .where(AuditLogRecord.workspace_id == self.workspace_id)
                .order_by(AuditLogRecord.created_at.asc())
            ).all()
        return {
            "map": self.current_map().model_dump(by_alias=True),
            "drafts": {
                row.draft_id: {
                    "draftId": row.draft_id,
                    "map": row.map_json,
                    "status": row.status,
                    "createdAt": iso_datetime(row.created_at),
                    "publishedAt": iso_datetime(row.published_at) if row.published_at else None,
                }
                for row in drafts
            },
            "robots": [robot.model_dump() for robot in self.robots()],
            "messages": [message.model_dump() for message in reversed(self.messages(limit=1000))],
            "auditLogs": [self._audit_dict(row) for row in audits],
        }

    def list_scenarios(self) -> list[ScenarioSummary]:
        return [self._default_scenario()]

    def get_scenario(self, scenario_id: str) -> ScenarioSummary | None:
        scenario = self._default_scenario()
        return scenario if scenario.scenarioId == scenario_id else None

    def list_task_templates(self) -> list[TaskTemplate]:
        return self.TASK_TEMPLATES

    def create_simulation_run(self, request: SimulationRunCreate) -> SimulationRun:
        scenario = self.get_scenario(request.scenarioId)
        if scenario is None:
            raise ValueError("scenario not found")
        run_id = protocol_id("RUN")
        created_at = now_utc()
        scenario_json = scenario.model_dump(by_alias=True)
        with self.database.session() as session:
            row = SimulationRunRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                scenario_id=scenario.scenarioId,
                name=request.name or f"{scenario.name} run",
                status="Draft",
                map_id=scenario.siteMapId,
                map_version=scenario.siteMapVersion,
                scenario_json=scenario_json,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            session.add(self._initial_current_state_row(run_id, scenario_json, created_at))
            self._add_trace_span(
                session,
                run_id=run_id,
                task_id=None,
                trace_id=run_id,
                entity_type="SimulationRun",
                entity_id=run_id,
                operation="run.created",
                status="Completed",
                started_at=created_at,
                output_ref=f"run:{run_id}",
            )
            self._add_audit(session, "simulation.run.created", "simulation_run", run_id, after=scenario_json)
        return self.get_simulation_run(run_id)  # type: ignore[return-value]

    def list_simulation_runs(self, limit: int = 20) -> list[SimulationRun]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SimulationRunRecord)
                .where(SimulationRunRecord.workspace_id == self.workspace_id)
                .order_by(SimulationRunRecord.created_at.desc())
                .limit(limit)
            ).all()
            return [self._run_model(row) for row in rows]

    def get_simulation_run(self, run_id: str) -> SimulationRun | None:
        with self.database.session() as session:
            row = self._run_row(session, run_id)
            return self._run_model(row) if row else None

    def update_simulation_run_status(self, run_id: str, status: str) -> SimulationRun | None:
        now = now_utc()
        with self.database.session() as session:
            row = self._run_row(session, run_id)
            if row is None:
                return None
            row.status = status
            row.updated_at = now
            if status == "Running" and row.started_at is None:
                row.started_at = now
            if status in {"Stopped", "Succeeded", "Failed"}:
                row.finished_at = now
            self._add_trace_span(
                session,
                run_id=run_id,
                task_id=None,
                trace_id=run_id,
                entity_type="SimulationRun",
                entity_id=run_id,
                operation=f"run.{status.lower()}",
                status="Completed",
                started_at=now,
            )
        return self.get_simulation_run(run_id)

    def create_simulation_task(self, run_id: str, request: SimulationTaskCreate) -> SimulationTask | None:
        now = now_utc()
        task_id = protocol_id("TASK")
        trace_id = protocol_id("TRACE")
        steps = self._default_plan_steps(task_id, request.input)
        plan_id = protocol_id("PLAN")
        with self.database.session() as session:
            run = self._run_row(session, run_id)
            if run is None:
                return None
            task = SimulationTaskRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                task_id=task_id,
                trace_id=trace_id,
                goal=request.goal,
                input_json=request.input,
                constraints_json=request.constraints,
                priority=request.priority,
                expected_outcome=request.expectedOutcome,
                status="Ready",
                created_by=request.createdBy,
                created_at=now,
            )
            plan = SimulationPlanRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                task_id=task_id,
                plan_id=plan_id,
                trace_id=trace_id,
                plan_version=1,
                strategy="rule",
                steps_json=[step.model_dump() for step in steps],
                dependencies_json={},
                assumptions_json={"source": "rule-agent", "mode": "single-step"},
                generated_by="rule-agent",
                generation_latency_ms=0,
                status="Active",
                created_at=now,
                activated_at=now,
            )
            session.add(task)
            session.add(plan)
            for step in steps:
                session.add(
                    SimulationPlanStepRecord(
                        workspace_id=self.workspace_id,
                        run_id=run_id,
                        task_id=task_id,
                        plan_id=plan_id,
                        plan_step_id=step.planStepId,
                        sequence=step.sequence,
                        action_type=step.actionType,
                        target_json=step.target,
                        params_json=step.params,
                        depends_on_json=step.dependsOn,
                        success_condition=step.successCondition,
                        failure_policy=step.failurePolicy,
                        timeout_ms=step.timeoutMs,
                        status=step.status,
                    )
                )
            self._ensure_trace_header(session, run_id, task_id, trace_id, now)
            self._add_trace_span(
                session,
                run_id=run_id,
                task_id=task_id,
                trace_id=trace_id,
                entity_type="Task",
                entity_id=task_id,
                operation="task.created",
                status="Completed",
                started_at=now,
                output_ref=f"plan:{plan_id}",
            )
            self._refresh_current_state(session, run_id, None, now)
        return self.get_task(task_id)

    def create_task_from_template(self, run_id: str, request: TaskFromTemplateCreate) -> SimulationTask | None:
        template = next((item for item in self.TASK_TEMPLATES if item.templateId == request.templateId), None)
        if template is None:
            return None
        task_input = {**template.defaultInput, **request.parameters}
        return self.create_simulation_task(
            run_id,
            SimulationTaskCreate(
                goal=str(request.parameters.get("goal") or template.defaultGoal),
                input=task_input,
                constraints=dict(request.parameters.get("constraints") or {}),
                priority=int(request.parameters.get("priority") or 5),
                expectedOutcome=str(request.parameters.get("expectedOutcome") or template.description),
                createdBy=request.createdBy,
            ),
        )

    def get_task(self, task_id: str) -> SimulationTask | None:
        with self.database.session() as session:
            task = session.scalar(
                select(SimulationTaskRecord).where(
                    SimulationTaskRecord.workspace_id == self.workspace_id,
                    SimulationTaskRecord.task_id == task_id,
                )
            )
            if task is None:
                return None
            plan = self._active_plan_row(session, task_id)
            return self._task_model(task, self._plan_model(plan) if plan else None)

    def list_run_tasks(self, run_id: str) -> list[SimulationTask]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SimulationTaskRecord)
                .where(
                    SimulationTaskRecord.workspace_id == self.workspace_id,
                    SimulationTaskRecord.run_id == run_id,
                )
                .order_by(SimulationTaskRecord.created_at.asc())
            ).all()
            tasks: list[SimulationTask] = []
            for row in rows:
                plan = self._active_plan_row(session, row.task_id)
                tasks.append(self._task_model(row, self._plan_model(plan) if plan else None))
            return tasks

    def list_task_plans(self, task_id: str) -> list[SimulationPlan]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SimulationPlanRecord)
                .where(
                    SimulationPlanRecord.workspace_id == self.workspace_id,
                    SimulationPlanRecord.task_id == task_id,
                )
                .order_by(SimulationPlanRecord.plan_version.asc())
            ).all()
            return [self._plan_model(row) for row in rows]

    def create_action(self, request: ActionCreate) -> SimulationAction | None:
        now = now_utc()
        action_id = protocol_id("ACTION")
        robot_code = request.robotCode or self._default_robot_code()
        with self.database.session() as session:
            run = self._run_row(session, request.runId)
            if run is None:
                return None
            task = None
            if request.taskId:
                task = session.scalar(
                    select(SimulationTaskRecord).where(
                        SimulationTaskRecord.workspace_id == self.workspace_id,
                        SimulationTaskRecord.task_id == request.taskId,
                        SimulationTaskRecord.run_id == request.runId,
                    )
                )
                if task is None:
                    return None
            plan = None
            if request.planId:
                plan = session.scalar(
                    select(SimulationPlanRecord).where(
                        SimulationPlanRecord.workspace_id == self.workspace_id,
                        SimulationPlanRecord.plan_id == request.planId,
                    )
                )
            elif task:
                plan = self._active_plan_row(session, task.task_id)
            trace_id = task.trace_id if task else protocol_id("TRACE")
            if task is None:
                self._ensure_trace_header(session, request.runId, None, trace_id, now)
            row = SimulationActionRecord(
                workspace_id=self.workspace_id,
                run_id=request.runId,
                task_id=task.task_id if task else request.taskId,
                plan_id=plan.plan_id if plan else request.planId,
                plan_step_id=request.planStepId,
                action_id=action_id,
                trace_id=trace_id,
                robot_code=robot_code,
                command=request.command,
                params_json=request.params,
                attempt_no=1,
                timeout_ms=request.timeoutMs,
                status="Pending",
                created_at=now,
            )
            session.add(row)
            self._add_trace_span(
                session,
                run_id=request.runId,
                task_id=row.task_id,
                trace_id=trace_id,
                entity_type="Action",
                entity_id=action_id,
                operation="action.created",
                status="Completed",
                started_at=now,
            )
            self._refresh_current_state(session, request.runId, None, now)
        return self.get_action(action_id)

    def mark_action_issued(
        self,
        action_id: str,
        command_id: str,
        request_id: str | None,
        result: dict[str, Any],
    ) -> SimulationAction | None:
        now = now_utc()
        with self.database.session() as session:
            row = self._action_row(session, action_id)
            if row is None:
                return None
            row.command_id = command_id
            row.request_id = request_id
            row.status = "Issued"
            row.issued_at = now
            row.result_json = result
            self._add_trace_span(
                session,
                run_id=row.run_id,
                task_id=row.task_id,
                trace_id=row.trace_id,
                entity_type="Command",
                entity_id=command_id,
                operation="command.issued",
                status="Completed" if result.get("mqttPublished") else "Degraded",
                started_at=now,
                input_ref=f"action:{row.action_id}",
                output_ref=f"message:{command_id}",
            )
            self._refresh_current_state(session, row.run_id, None, now)
        return self.get_action(action_id)

    def get_action(self, action_id: str) -> SimulationAction | None:
        with self.database.session() as session:
            row = self._action_row(session, action_id)
            return self._action_model(row) if row else None

    def list_actions(self, run_id: str | None = None, task_id: str | None = None, limit: int = 100) -> list[SimulationAction]:
        query = select(SimulationActionRecord).where(SimulationActionRecord.workspace_id == self.workspace_id)
        if run_id:
            query = query.where(SimulationActionRecord.run_id == run_id)
        if task_id:
            query = query.where(SimulationActionRecord.task_id == task_id)
        with self.database.session() as session:
            rows = session.scalars(query.order_by(SimulationActionRecord.created_at.desc()).limit(limit)).all()
            return [self._action_model(row) for row in rows]

    def stop_action(self, action_id: str) -> SimulationAction | None:
        now = now_utc()
        with self.database.session() as session:
            row = self._action_row(session, action_id)
            if row is None:
                return None
            row.status = "Stopped"
            row.finished_at = now
            self._add_trace_span(
                session,
                run_id=row.run_id,
                task_id=row.task_id,
                trace_id=row.trace_id,
                entity_type="Action",
                entity_id=row.action_id,
                operation="action.stopped",
                status="Completed",
                started_at=now,
            )
            self._refresh_current_state(session, row.run_id, None, now)
        return self.get_action(action_id)

    def ingest_observation_from_message(self, message: MessageRecord) -> Observation | None:
        payload = message.payload
        event_name = payload.get("event")
        if not event_name:
            return None
        command_id = payload.get("commandId")
        task_id = payload.get("taskId")
        trace_id = payload.get("traceId")
        now = parse_datetime(payload.get("timestamp") or message.createdAt)
        with self.database.session() as session:
            action = None
            if command_id:
                action = session.scalar(
                    select(SimulationActionRecord)
                    .where(
                        SimulationActionRecord.workspace_id == self.workspace_id,
                        SimulationActionRecord.command_id == command_id,
                    )
                    .order_by(SimulationActionRecord.created_at.desc())
                    .limit(1)
                )
            task = None
            if task_id:
                task = session.scalar(
                    select(SimulationTaskRecord).where(
                        SimulationTaskRecord.workspace_id == self.workspace_id,
                        SimulationTaskRecord.task_id == task_id,
                    )
                )
            if task is None and action and action.task_id:
                task = session.scalar(
                    select(SimulationTaskRecord).where(
                        SimulationTaskRecord.workspace_id == self.workspace_id,
                        SimulationTaskRecord.task_id == action.task_id,
                    )
                )
            run_id = action.run_id if action else (task.run_id if task else None)
            if run_id is None:
                return None
            observation = self._add_observation(
                session,
                run_id=run_id,
                task_id=task.task_id if task else (action.task_id if action else task_id),
                action_id=action.action_id if action else None,
                trace_id=trace_id or (action.trace_id if action else (task.trace_id if task else None)),
                source=message.source,
                event=str(event_name),
                event_id=payload.get("eventId"),
                message_id=message.messageId,
                robot_code=payload.get("robotCode") or payload.get("robotId"),
                command_id=command_id,
                request_id=payload.get("requestId"),
                timestamp=now,
                data=payload.get("data") or {},
                error=payload.get("error"),
            )
            if action:
                self._apply_action_event(action, str(event_name), now, payload)
            if task:
                self._apply_task_event(task, str(event_name), now)
            self._add_trace_span(
                session,
                run_id=run_id,
                task_id=task.task_id if task else None,
                trace_id=observation.trace_id or run_id,
                entity_type="Observation",
                entity_id=observation.observation_id,
                operation=f"observation.{event_name}",
                status=observation.processing_status,
                started_at=now,
                input_ref=f"message:{message.messageId}",
                output_ref=f"state:{run_id}",
                error_ref=f"error:{observation.observation_id}" if observation.error_json else None,
            )
            self._refresh_current_state(session, run_id, observation, now)
            return self._observation_model(observation)

    def inject_simulation_event(self, run_id: str, request: SimulationEventCreate) -> Observation | None:
        timestamp = now_utc()
        event_id = protocol_id("EVT")
        with self.database.session() as session:
            run = self._run_row(session, run_id)
            if run is None:
                return None
            active_task = self._latest_active_task(session, run_id)
            trace_id = active_task.trace_id if active_task else run_id
        payload = {
            "schemaVersion": "1.0",
            "messageType": "event",
            "event": request.eventType,
            "eventId": event_id,
            "commandId": None,
            "taskId": active_task.task_id if active_task else None,
            "requestId": None,
            "robotCode": request.targetId if request.targetType == "robot" else None,
            "traceId": trace_id,
            "source": "simulation-console",
            "timestamp": iso_datetime(timestamp),
            "data": {
                "targetType": request.targetType,
                "targetId": request.targetId,
                "severity": request.severity,
                "durationMs": request.durationMs,
                "autoRecover": request.autoRecover,
                **request.data,
            },
            "error": {"code": request.eventType, "message": request.eventType} if request.severity in {"error", "critical"} else None,
        }
        message = MessageRecord(
            messageId=event_id,
            messageType="event",
            source="simulation-console",
            topic=f"simulation/runs/{run_id}/events",
            createdAt=payload["timestamp"],
            payload=payload,
        )
        self.append_message(message)
        return self.ingest_observation_from_message(message)

    def list_observations(self, run_id: str, limit: int = 100) -> list[Observation]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SimulationObservationRecord)
                .where(
                    SimulationObservationRecord.workspace_id == self.workspace_id,
                    SimulationObservationRecord.run_id == run_id,
                )
                .order_by(SimulationObservationRecord.timestamp.desc())
                .limit(limit)
            ).all()
            return [self._observation_model(row) for row in rows]

    def get_current_state(self, run_id: str) -> CurrentState | None:
        with self.database.session() as session:
            row = self._current_state_row(session, run_id)
            return self._current_state_model(row) if row else None

    def create_snapshot(self, run_id: str, request: SnapshotCreate) -> Snapshot | None:
        now = now_utc()
        with self.database.session() as session:
            state = self._current_state_row(session, run_id)
            if state is None:
                return None
            latest_task = self._latest_active_task(session, run_id)
            snapshot_payload = self._current_state_model(state).model_dump()
            checksum = hashlib.sha256(json.dumps(snapshot_payload, sort_keys=True).encode("utf-8")).hexdigest()
            snapshot_id = protocol_id("SNAP")
            row = SimulationSnapshotRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                task_id=latest_task.task_id if latest_task else None,
                trace_id=latest_task.trace_id if latest_task else run_id,
                snapshot_id=snapshot_id,
                state_version=state.state_version,
                reason=request.reason,
                snapshot_json=snapshot_payload,
                checksum=checksum,
                created_at=now,
            )
            session.add(row)
            self._add_trace_span(
                session,
                run_id=run_id,
                task_id=row.task_id,
                trace_id=row.trace_id or run_id,
                entity_type="Snapshot",
                entity_id=snapshot_id,
                operation="snapshot.created",
                status="Completed",
                started_at=now,
                output_ref=f"snapshot:{snapshot_id}",
            )
            return self._snapshot_model(row)

    def list_snapshots(self, run_id: str) -> list[Snapshot]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SimulationSnapshotRecord)
                .where(
                    SimulationSnapshotRecord.workspace_id == self.workspace_id,
                    SimulationSnapshotRecord.run_id == run_id,
                )
                .order_by(SimulationSnapshotRecord.created_at.desc())
            ).all()
            return [self._snapshot_model(row) for row in rows]

    def get_trace(self, trace_id: str) -> TraceResponse:
        with self.database.session() as session:
            header = session.scalar(
                select(SimulationTraceHeaderRecord).where(
                    SimulationTraceHeaderRecord.workspace_id == self.workspace_id,
                    SimulationTraceHeaderRecord.trace_id == trace_id,
                )
            )
            spans = session.scalars(
                select(SimulationTraceSpanRecord)
                .where(
                    SimulationTraceSpanRecord.workspace_id == self.workspace_id,
                    SimulationTraceSpanRecord.trace_id == trace_id,
                )
                .order_by(SimulationTraceSpanRecord.started_at.asc())
            ).all()
            return TraceResponse(
                traceId=trace_id,
                runId=header.run_id if header else (spans[0].run_id if spans else None),
                taskId=header.task_id if header else (spans[0].task_id if spans else None),
                status=header.status if header else ("Open" if spans else "NotFound"),
                startedAt=iso_datetime(header.started_at) if header else (iso_datetime(spans[0].started_at) if spans else None),
                finishedAt=iso_datetime(header.finished_at) if header and header.finished_at else None,
                durationMs=header.duration_ms if header else None,
                spans=[self._trace_span_model(row) for row in spans],
            )

    def list_run_messages(self, run_id: str, limit: int = 100, category: str | None = None) -> list[MessageRecord]:
        with self.database.session() as session:
            tasks = session.scalars(
                select(SimulationTaskRecord.task_id).where(
                    SimulationTaskRecord.workspace_id == self.workspace_id,
                    SimulationTaskRecord.run_id == run_id,
                )
            ).all()
            traces = session.scalars(
                select(SimulationTaskRecord.trace_id).where(
                    SimulationTaskRecord.workspace_id == self.workspace_id,
                    SimulationTaskRecord.run_id == run_id,
                )
            ).all()
            commands = session.scalars(
                select(SimulationActionRecord.command_id).where(
                    SimulationActionRecord.workspace_id == self.workspace_id,
                    SimulationActionRecord.run_id == run_id,
                    SimulationActionRecord.command_id.is_not(None),
                )
            ).all()
            query = select(MessageRecordRow).where(MessageRecordRow.workspace_id == self.workspace_id)
            filters = []
            if tasks:
                filters.append(MessageRecordRow.task_id.in_(tasks))
            if traces:
                filters.append(MessageRecordRow.trace_id.in_(traces))
            if commands:
                filters.append(MessageRecordRow.command_id.in_(commands))
            filters.append(MessageRecordRow.payload_json["runId"].as_string() == run_id)
            if filters:
                query = query.where(or_(*filters))
            rows = session.scalars(query.order_by(MessageRecordRow.created_at.desc()).limit(limit)).all()
            messages = [self._message_model(row) for row in rows]
            if category:
                return [message for message in messages if self._message_category(message) == category]
            return messages

    def export_simulation_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_simulation_run(run_id)
        if run is None:
            return None
        tasks = self.list_run_tasks(run_id)
        actions = self.list_actions(run_id=run_id, limit=1000)
        observations = self.list_observations(run_id, limit=1000)
        current_state = self.get_current_state(run_id)
        snapshots = self.list_snapshots(run_id)
        trace_ids = sorted({task.traceId for task in tasks} | {action.traceId for action in actions})
        return {
            "manifest": {
                "schemaVersion": "1.0",
                "exportType": "simulation_run",
                "runId": run_id,
                "createdAt": utc_now(),
                "secretPolicy": "secrets-excluded",
            },
            "workspace": {"workspaceId": str(self.workspace_id)},
            "run": run.model_dump(),
            "tasks": [task.model_dump() for task in tasks],
            "actions": [action.model_dump() for action in actions],
            "observations": [observation.model_dump() for observation in observations],
            "currentState": current_state.model_dump() if current_state else None,
            "snapshots": [snapshot.model_dump() for snapshot in snapshots],
            "traces": [self.get_trace(trace_id).model_dump() for trace_id in trace_ids],
            "mqttMessages": [message.model_dump() for message in self.list_run_messages(run_id, limit=1000)],
            "audit": {"note": "audit log remains in workspace export"},
        }

    def append_audit(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        with self.database.session() as session:
            self._add_audit(session, action, resource_type, resource_id, before, after)

    def _site_map_row(self, map_data: dict[str, Any], status: str) -> SiteMapRecord:
        return SiteMapRecord(
            workspace_id=self.workspace_id,
            map_id=map_data["id"],
            map_name=map_data.get("name", map_data["id"]),
            config_version=map_data.get("configVersion", "v0.1.0"),
            status=status,
            map_json=map_data,
            created_at=now_utc(),
            updated_at=now_utc(),
        )

    def _default_scenario(self) -> ScenarioSummary:
        current_map = self.current_map()
        robots = self.robots()
        robot_codes = [robot.robotId for robot in robots]
        return ScenarioSummary(
            scenarioId="default-site-a",
            name=f"{current_map.name} / {current_map.configVersion}",
            siteMapId=current_map.id,
            siteMapVersion=current_map.configVersion,
            robotCodes=robot_codes,
            robotTypeIds=sorted({robot.robotType for robot in robots}) or ["machine-dog"],
            actionSet={"actionSetId": "machine-dog-basic", "commands": ["goto_pose", "where", "stop"]},
            taskFlow={"taskFlowId": "basic-task-flow", "modes": ["manual", "template"]},
            resourceProfile={"resourceProfileId": "default", "resourceLocks": [], "pathCapacity": "map.pathEdges.capacity"},
            map=current_map,
        )

    def _initial_current_state_row(
        self,
        run_id: str,
        scenario_json: dict[str, Any],
        created_at: datetime,
    ) -> SimulationCurrentStateRecord:
        return SimulationCurrentStateRecord(
            workspace_id=self.workspace_id,
            run_id=run_id,
            state_version=1,
            task_state_json={"status": "NoTask", "activeTaskId": None, "progress": 0},
            active_plan_json=None,
            robot_states_json=[robot.model_dump() for robot in self.robots()],
            resource_states_json={"locks": [], "blockedPaths": [], "stations": []},
            environment_state_json={
                "scenarioId": scenario_json["scenarioId"],
                "mapId": scenario_json["siteMapId"],
                "mapVersion": scenario_json["siteMapVersion"],
                "alerts": [],
            },
            pending_actions_json=[],
            active_events_json=[],
            updated_at=created_at,
        )

    def _run_row(self, session: Any, run_id: str) -> SimulationRunRecord | None:
        return session.scalar(
            select(SimulationRunRecord).where(
                SimulationRunRecord.workspace_id == self.workspace_id,
                SimulationRunRecord.run_id == run_id,
            )
        )

    @staticmethod
    def _run_model(row: SimulationRunRecord) -> SimulationRun:
        return SimulationRun(
            runId=row.run_id,
            scenarioId=row.scenario_id,
            name=row.name,
            status=row.status,
            mapId=row.map_id,
            mapVersion=row.map_version,
            scenario=row.scenario_json,
            createdAt=iso_datetime(row.created_at),
            startedAt=iso_datetime(row.started_at) if row.started_at else None,
            finishedAt=iso_datetime(row.finished_at) if row.finished_at else None,
            updatedAt=iso_datetime(row.updated_at),
        )

    def _default_plan_steps(self, task_id: str, task_input: dict[str, Any]) -> list[PlanStep]:
        command = task_input.get("command") or "goto_pose"
        target = dict(task_input.get("target") or {})
        if command == "goto_pose" and not {"x", "y"}.issubset(target):
            stations = [item for item in self.current_map().objects if item.type == "station"]
            target_station = stations[-1] if stations else None
            target = {
                "x": target_station.x if target_station else 760,
                "y": target_station.y if target_station else 420,
                "z": 0,
                "yaw": 0,
            }
        return [
            PlanStep(
                planStepId=protocol_id("STEP"),
                sequence=1,
                actionType=str(command),
                target=target,
                params=target if command == "goto_pose" else {},
                successCondition="result event task.succeeded or where.result",
                failurePolicy="surface_to_operator",
                timeoutMs=int(task_input.get("timeoutMs") or 60000),
            )
        ]

    def _active_plan_row(self, session: Any, task_id: str) -> SimulationPlanRecord | None:
        return session.scalar(
            select(SimulationPlanRecord)
            .where(
                SimulationPlanRecord.workspace_id == self.workspace_id,
                SimulationPlanRecord.task_id == task_id,
            )
            .order_by(SimulationPlanRecord.plan_version.desc())
            .limit(1)
        )

    def _latest_active_task(self, session: Any, run_id: str) -> SimulationTaskRecord | None:
        return session.scalar(
            select(SimulationTaskRecord)
            .where(
                SimulationTaskRecord.workspace_id == self.workspace_id,
                SimulationTaskRecord.run_id == run_id,
            )
            .order_by(SimulationTaskRecord.created_at.desc())
            .limit(1)
        )

    @staticmethod
    def _plan_model(row: SimulationPlanRecord) -> SimulationPlan:
        steps = [PlanStep.model_validate(step) for step in row.steps_json]
        return SimulationPlan(
            planId=row.plan_id,
            runId=row.run_id,
            taskId=row.task_id,
            traceId=row.trace_id,
            planVersion=row.plan_version,
            strategy=row.strategy,
            steps=steps,
            dependencies=row.dependencies_json,
            assumptions=row.assumptions_json,
            generatedBy=row.generated_by,
            generationLatencyMs=row.generation_latency_ms,
            status=row.status,
            createdAt=iso_datetime(row.created_at),
            activatedAt=iso_datetime(row.activated_at) if row.activated_at else None,
        )

    @staticmethod
    def _task_model(row: SimulationTaskRecord, active_plan: SimulationPlan | None = None) -> SimulationTask:
        return SimulationTask(
            taskId=row.task_id,
            runId=row.run_id,
            traceId=row.trace_id,
            goal=row.goal,
            input=row.input_json,
            constraints=row.constraints_json,
            priority=row.priority,
            expectedOutcome=row.expected_outcome,
            status=row.status,
            createdBy=row.created_by,
            createdAt=iso_datetime(row.created_at),
            startedAt=iso_datetime(row.started_at) if row.started_at else None,
            finishedAt=iso_datetime(row.finished_at) if row.finished_at else None,
            activePlan=active_plan,
        )

    def _action_row(self, session: Any, action_id: str) -> SimulationActionRecord | None:
        return session.scalar(
            select(SimulationActionRecord).where(
                SimulationActionRecord.workspace_id == self.workspace_id,
                SimulationActionRecord.action_id == action_id,
            )
        )

    @staticmethod
    def _action_model(row: SimulationActionRecord) -> SimulationAction:
        return SimulationAction(
            actionId=row.action_id,
            runId=row.run_id,
            taskId=row.task_id,
            planId=row.plan_id,
            planStepId=row.plan_step_id,
            traceId=row.trace_id,
            robotCode=row.robot_code,
            command=row.command,
            params=row.params_json,
            commandId=row.command_id,
            requestId=row.request_id,
            attemptNo=row.attempt_no,
            timeoutMs=row.timeout_ms,
            status=row.status,
            result=row.result_json,
            createdAt=iso_datetime(row.created_at),
            issuedAt=iso_datetime(row.issued_at) if row.issued_at else None,
            startedAt=iso_datetime(row.started_at) if row.started_at else None,
            finishedAt=iso_datetime(row.finished_at) if row.finished_at else None,
        )

    def _default_robot_code(self) -> str:
        robots = self.robots()
        return robots[0].robotId if robots else "robot-001"

    def _add_observation(
        self,
        session: Any,
        run_id: str,
        task_id: str | None,
        action_id: str | None,
        trace_id: str | None,
        source: str,
        event: str,
        event_id: str | None,
        message_id: str | None,
        robot_code: str | None,
        command_id: str | None,
        request_id: str | None,
        timestamp: datetime,
        data: dict[str, Any],
        error: dict[str, Any] | None,
    ) -> SimulationObservationRecord:
        observation_id = event_id or protocol_id("OBS")
        existing = session.scalar(
            select(SimulationObservationRecord).where(
                SimulationObservationRecord.workspace_id == self.workspace_id,
                SimulationObservationRecord.observation_id == observation_id,
            )
        )
        if existing:
            return existing
        row = SimulationObservationRecord(
            workspace_id=self.workspace_id,
            run_id=run_id,
            task_id=task_id,
            action_id=action_id,
            trace_id=trace_id,
            observation_id=observation_id,
            source=source,
            event=event,
            category=self._event_category(event),
            event_id=event_id,
            message_id=message_id,
            robot_code=robot_code,
            command_id=command_id,
            request_id=request_id,
            timestamp=timestamp,
            data_json=data,
            error_json=error,
            processing_status="Applied",
        )
        session.add(row)
        return row

    @staticmethod
    def _event_category(event: str) -> str:
        if event in {"command.accepted", "command.rejected"}:
            return "Ack"
        if event in {"pose.updated", "where.result"}:
            return "Telemetry"
        if event in {
            "task.failed",
            "task.timeout",
            "where.failed",
            "device.offline",
            "robot.offline",
            "action.failed",
            "path.blocked",
            "interface.timeout",
            "message.dropped",
            "resource.locked",
            "station.unavailable",
            "battery.low",
        }:
            return "Alert"
        return "Event"

    def _message_category(self, message: MessageRecord) -> str:
        if message.messageType == "command":
            return "Command"
        return self._event_category(str(message.payload.get("event") or message.messageType))

    @staticmethod
    def _observation_model(row: SimulationObservationRecord) -> Observation:
        return Observation(
            observationId=row.observation_id,
            runId=row.run_id,
            taskId=row.task_id,
            actionId=row.action_id,
            traceId=row.trace_id,
            source=row.source,
            event=row.event,
            category=row.category,
            eventId=row.event_id,
            messageId=row.message_id,
            robotCode=row.robot_code,
            commandId=row.command_id,
            requestId=row.request_id,
            timestamp=iso_datetime(row.timestamp),
            data=row.data_json,
            error=row.error_json,
            processingStatus=row.processing_status,
        )

    @staticmethod
    def _apply_action_event(
        action: SimulationActionRecord,
        event: str,
        timestamp: datetime,
        payload: dict[str, Any],
    ) -> None:
        if event == "command.accepted":
            action.status = "Accepted"
        elif event == "task.started":
            action.status = "Running"
            action.started_at = action.started_at or timestamp
        elif event in {"task.succeeded", "where.result"}:
            action.status = "Succeeded"
            action.finished_at = timestamp
        elif event == "command.rejected":
            action.status = "Rejected"
            action.finished_at = timestamp
        elif event in {"task.failed", "where.failed", "action.failed"}:
            action.status = "Failed"
            action.finished_at = timestamp
        elif event in {"task.stopped"}:
            action.status = "Stopped"
            action.finished_at = timestamp
        elif event in {"task.timeout", "interface.timeout"}:
            action.status = "Timeout"
            action.finished_at = timestamp
        action.result_json = {"lastEvent": event, "data": payload.get("data"), "error": payload.get("error")}

    @staticmethod
    def _apply_task_event(task: SimulationTaskRecord, event: str, timestamp: datetime) -> None:
        if event == "task.started":
            task.status = "Running"
            task.started_at = task.started_at or timestamp
        elif event in {"task.succeeded", "where.result"}:
            task.status = "Succeeded"
            task.finished_at = timestamp
        elif event in {"task.failed", "where.failed", "task.timeout", "interface.timeout", "action.failed"}:
            task.status = "Failed"
            task.finished_at = timestamp
        elif event == "task.stopped":
            task.status = "Cancelled"
            task.finished_at = timestamp

    def _current_state_row(self, session: Any, run_id: str) -> SimulationCurrentStateRecord | None:
        return session.scalar(
            select(SimulationCurrentStateRecord).where(
                SimulationCurrentStateRecord.workspace_id == self.workspace_id,
                SimulationCurrentStateRecord.run_id == run_id,
            )
        )

    def _refresh_current_state(
        self,
        session: Any,
        run_id: str,
        observation: SimulationObservationRecord | None,
        timestamp: datetime,
    ) -> None:
        row = self._current_state_row(session, run_id)
        if row is None:
            return
        latest_task = self._latest_active_task(session, run_id)
        active_plan = self._active_plan_row(session, latest_task.task_id) if latest_task else None
        pending_actions = session.scalars(
            select(SimulationActionRecord)
            .where(
                SimulationActionRecord.workspace_id == self.workspace_id,
                SimulationActionRecord.run_id == run_id,
                SimulationActionRecord.status.not_in(self.ACTION_TERMINAL_STATUSES),
            )
            .order_by(SimulationActionRecord.created_at.asc())
            .limit(20)
        ).all()
        active_events = list(row.active_events_json or [])
        resource_states = dict(row.resource_states_json or {})
        environment_state = dict(row.environment_state_json or {})
        if observation:
            event_item = {
                "event": observation.event,
                "category": observation.category,
                "targetId": observation.robot_code or observation.data_json.get("targetId"),
                "severity": observation.data_json.get("severity"),
                "timestamp": iso_datetime(observation.timestamp),
            }
            if observation.category == "Alert":
                active_events = [event_item, *active_events][:20]
                environment_state["alerts"] = active_events
            if observation.event == "path.blocked":
                blocked_paths = list(resource_states.get("blockedPaths") or [])
                target_id = observation.data_json.get("targetId")
                if target_id and target_id not in blocked_paths:
                    blocked_paths.append(target_id)
                resource_states["blockedPaths"] = blocked_paths
            if observation.event == "resource.locked":
                locks = list(resource_states.get("locks") or [])
                locks.append({"resourceId": observation.data_json.get("targetId"), "timestamp": iso_datetime(timestamp)})
                resource_states["locks"] = locks[-20:]
        row.state_version += 1
        row.task_state_json = {
            "activeTaskId": latest_task.task_id if latest_task else None,
            "status": latest_task.status if latest_task else "NoTask",
            "goal": latest_task.goal if latest_task else None,
            "progress": self._task_progress(latest_task.status) if latest_task else 0,
        }
        row.active_plan_json = self._plan_model(active_plan).model_dump() if active_plan else None
        row.robot_states_json = [robot.model_dump() for robot in self.robots()]
        row.resource_states_json = resource_states
        row.environment_state_json = environment_state
        row.pending_actions_json = [self._action_model(action).model_dump() for action in pending_actions]
        row.active_events_json = active_events
        row.last_observation_id = observation.observation_id if observation else row.last_observation_id
        row.last_observation_at = observation.timestamp if observation else row.last_observation_at
        row.updated_at = timestamp

    @staticmethod
    def _task_progress(status: str) -> int:
        return {
            "Draft": 0,
            "Ready": 10,
            "Running": 60,
            "Succeeded": 100,
            "Failed": 100,
            "Cancelled": 100,
        }.get(status, 0)

    @staticmethod
    def _current_state_model(row: SimulationCurrentStateRecord) -> CurrentState:
        return CurrentState(
            runId=row.run_id,
            stateVersion=row.state_version,
            taskState=row.task_state_json,
            activePlan=row.active_plan_json,
            robotStates=row.robot_states_json,
            resourceStates=row.resource_states_json,
            environmentState=row.environment_state_json,
            pendingActions=row.pending_actions_json,
            activeEvents=row.active_events_json,
            lastObservationId=row.last_observation_id,
            lastObservationAt=iso_datetime(row.last_observation_at) if row.last_observation_at else None,
            updatedAt=iso_datetime(row.updated_at),
        )

    @staticmethod
    def _snapshot_model(row: SimulationSnapshotRecord) -> Snapshot:
        return Snapshot(
            snapshotId=row.snapshot_id,
            runId=row.run_id,
            taskId=row.task_id,
            traceId=row.trace_id,
            stateVersion=row.state_version,
            reason=row.reason,
            snapshot=row.snapshot_json,
            checksum=row.checksum,
            createdAt=iso_datetime(row.created_at),
        )

    def _ensure_trace_header(
        self,
        session: Any,
        run_id: str,
        task_id: str | None,
        trace_id: str,
        started_at: datetime,
    ) -> None:
        existing = session.scalar(
            select(SimulationTraceHeaderRecord).where(
                SimulationTraceHeaderRecord.workspace_id == self.workspace_id,
                SimulationTraceHeaderRecord.trace_id == trace_id,
            )
        )
        if existing:
            return
        session.add(
            SimulationTraceHeaderRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                task_id=task_id,
                trace_id=trace_id,
                status="Open",
                started_at=started_at,
            )
        )

    def _add_trace_span(
        self,
        session: Any,
        run_id: str,
        task_id: str | None,
        trace_id: str,
        entity_type: str,
        entity_id: str,
        operation: str,
        status: str,
        started_at: datetime,
        input_ref: str | None = None,
        output_ref: str | None = None,
        error_ref: str | None = None,
    ) -> None:
        self._ensure_trace_header(session, run_id, task_id, trace_id, started_at)
        session.add(
            SimulationTraceSpanRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                task_id=task_id,
                trace_id=trace_id,
                span_id=protocol_id("SPAN"),
                parent_span_id=None,
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                status=status,
                started_at=started_at,
                finished_at=started_at,
                duration_ms=0,
                input_ref=input_ref,
                output_ref=output_ref,
                error_ref=error_ref,
            )
        )

    @staticmethod
    def _trace_span_model(row: SimulationTraceSpanRecord) -> TraceSpan:
        return TraceSpan(
            spanId=row.span_id,
            parentSpanId=row.parent_span_id,
            traceId=row.trace_id,
            runId=row.run_id,
            taskId=row.task_id,
            entityType=row.entity_type,
            entityId=row.entity_id,
            operation=row.operation,
            status=row.status,
            startedAt=iso_datetime(row.started_at),
            finishedAt=iso_datetime(row.finished_at) if row.finished_at else None,
            durationMs=row.duration_ms,
            inputRef=row.input_ref,
            outputRef=row.output_ref,
            errorRef=row.error_ref,
        )

    def _robot_row(self, robot: RobotState) -> RobotInstanceRecord:
        return RobotInstanceRecord(
            workspace_id=self.workspace_id,
            robot_code=robot.robotId,
            robot_type=robot.robotType,
            robot_state=robot.state,
            x=robot.x,
            y=robot.y,
            progress=robot.progress,
            current_action=robot.currentAction,
            updated_at=parse_datetime(robot.updatedAt),
        )

    @staticmethod
    def _robot_model(row: RobotInstanceRecord) -> RobotState:
        return RobotState(
            robotId=row.robot_code,
            robotType=row.robot_type,
            state=row.robot_state,
            x=row.x,
            y=row.y,
            progress=row.progress,
            currentAction=row.current_action,
            updatedAt=iso_datetime(row.updated_at),
        )

    def _message_row(self, message: MessageRecord) -> MessageRecordRow:
        payload = message.payload
        inner_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
        return MessageRecordRow(
            workspace_id=self.workspace_id,
            message_id=message.messageId,
            message_type=message.messageType,
            source=message.source,
            topic=message.topic,
            command_id=payload.get("commandId") or payload.get("correlationId") or inner_payload.get("commandId"),
            task_id=payload.get("taskId"),
            request_id=payload.get("requestId"),
            trace_id=payload.get("traceId"),
            robot_code=payload.get("robotCode") or payload.get("robotId") or inner_payload.get("robotCode"),
            event=payload.get("event"),
            created_at=parse_datetime(message.createdAt),
            payload_json=payload,
        )

    @staticmethod
    def _message_model(row: MessageRecordRow) -> MessageRecord:
        return MessageRecord(
            messageId=row.message_id,
            messageType=row.message_type,
            source=row.source,
            topic=row.topic,
            createdAt=iso_datetime(row.created_at),
            payload=row.payload_json,
        )

    def _add_audit(
        self,
        session: Any,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            AuditLogRecord(
                workspace_id=self.workspace_id,
                audit_id=new_id("audit"),
                actor_id="system",
                actor_type="service",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                before_json=before,
                after_json=after,
                created_at=now_utc(),
            )
        )

    @staticmethod
    def _audit_dict(row: AuditLogRecord) -> dict[str, Any]:
        return {
            "id": row.audit_id,
            "actorId": row.actor_id,
            "actorType": row.actor_type,
            "action": row.action,
            "resourceType": row.resource_type,
            "resourceId": row.resource_id,
            "before": row.before_json,
            "after": row.after_json,
            "createdAt": iso_datetime(row.created_at),
        }
