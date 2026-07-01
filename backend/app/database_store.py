from __future__ import annotations

import json
import hashlib
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .database import Database, workspace_id_from_env
from .db_models import (
    AuditLogRecord,
    Base,
    ExecutorInstanceRecord,
    ExportJobRecord,
    HubIdMappingRecord,
    MapDraftRecord,
    MessageRecordRow,
    RobotConfigRecord,
    RobotInstanceRecord,
    SimulationActionRecord,
    SimulationCurrentStateRecord,
    SimulationObservationRecord,
    SimulationPlanRecord,
    SimulationPlanStepRecord,
    SimulationRunRecord,
    SimulationSnapshotRecord,
    SimulationTaskChainItemRecord,
    SimulationTaskChainRecord,
    SimulationTaskRecord,
    SimulationTraceHeaderRecord,
    SimulationTraceSpanRecord,
    SiteMapRecord,
    TargetRegistryRecord,
    now_utc,
)
from .runtime_cache import RuntimeCache
from .schemas import (
    ActionCreate,
    BatchTaskCreate,
    BatchTaskResponse,
    CurrentState,
    ExecutorInstance,
    ExecutorInstanceCreate,
    HubIdMapping,
    MessageReplayCreate,
    MessageReplayResponse,
    MessageRecord,
    Observation,
    PlanStep,
    RobotConfig,
    RobotConfigCreate,
    RobotConfigUpdate,
    RobotState,
    ScenarioSummary,
    ScenarioValidationCheck,
    ScenarioValidationResponse,
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
    TaskChainItem,
    TaskFromTemplateCreate,
    TaskTemplate,
    TraceResponse,
    TraceSpan,
    SiteMap,
    TargetRegistryItem,
    TargetRegistryItemCreate,
    TargetRegistryItemUpdate,
    action_command_names,
    new_id,
    protocol_id,
    utc_now,
    validate_action_params,
)
from .store import (
    DATA_DIR,
    DEFAULT_MAP,
    EXPORT_DIR,
    STATE_PATH,
    JsonStore,
    default_executor_instances,
    default_robot_configs,
    default_robot_states,
    default_state,
    default_targets_from_map,
    map_source_targets_from_map,
    map_with_default_path_groups,
    target_registry_sync_issues,
)


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
            defaultInput={"command": "goto_pose", "target": {"x": 760, "y": 420, "z": 0, "yaw": 0, "speed": 1.0, "tolerance": 50}},
            supportedCommands=action_command_names(),
        ),
        TaskTemplate(
            templateId="pick-and-place",
            name="Pick and place",
            description="Simulate pick, carry and place as a single goto_pose execution step.",
            defaultGoal="Pick material and place it at the target station",
            defaultInput={"command": "goto_pose", "target": {"x": 220, "y": 240, "z": 0, "yaw": 0, "speed": 1.0, "tolerance": 50}},
            supportedCommands=["goto_pose", "pick", "place", "where", "stop"],
        ),
        TaskTemplate(
            templateId="inspect-point",
            name="Point inspection",
            description="Move to a configured point and report current pose.",
            defaultGoal="Inspect the configured point and report pose",
            defaultInput={"command": "where", "target": {}},
            supportedCommands=["where", "inspect", "goto_pose"],
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
        self.ensure_default_robot_fleet()
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

            for target in state.get("targets") or default_targets_from_map(map_data):
                session.add(self._target_row(TargetRegistryItem.model_validate(target)))

            for config in state.get("robotConfigs") or default_robot_configs():
                session.add(self._robot_config_row(RobotConfig.model_validate(config)))

            for executor in state.get("executors") or default_executor_instances():
                session.add(self._executor_row(ExecutorInstance.model_validate(executor)))

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

    def ensure_default_robot_fleet(self) -> None:
        default_robots = [RobotState.model_validate(robot) for robot in default_robot_states()]
        with self.database.session() as session:
            existing_codes = set(
                session.scalars(
                    select(RobotInstanceRecord.robot_code).where(
                        RobotInstanceRecord.workspace_id == self.workspace_id
                    )
                ).all()
            )
            for robot in default_robots:
                if robot.robotId not in existing_codes:
                    session.add(self._robot_row(robot))
                    existing_codes.add(robot.robotId)

            existing_config_codes = set(
                session.scalars(
                    select(RobotConfigRecord.robot_code).where(
                        RobotConfigRecord.workspace_id == self.workspace_id
                    )
                ).all()
            )
            for config_data in default_robot_configs():
                config = RobotConfig.model_validate(config_data)
                if config.robotCode not in existing_config_codes:
                    session.add(self._robot_config_row(config))
                    existing_config_codes.add(config.robotCode)

            existing_target_ids = set(
                session.scalars(
                    select(TargetRegistryRecord.target_id).where(
                        TargetRegistryRecord.workspace_id == self.workspace_id
                    )
                ).all()
            )
            try:
                target_source_map: dict[str, Any] | SiteMap = self.current_map()
            except RuntimeError:
                target_source_map = DEFAULT_MAP
            for target_data in default_targets_from_map(target_source_map):
                target = TargetRegistryItem.model_validate(target_data)
                if target.targetId not in existing_target_ids:
                    session.add(self._target_row(target))
                    existing_target_ids.add(target.targetId)
            self._sync_map_targets(session, target_source_map)

            existing_executor_ids = set(
                session.scalars(
                    select(ExecutorInstanceRecord.executor_id).where(
                        ExecutorInstanceRecord.workspace_id == self.workspace_id
                    )
                ).all()
            )
            for executor_data in default_executor_instances():
                executor = ExecutorInstance.model_validate(executor_data)
                if executor.executorId not in existing_executor_ids:
                    session.add(self._executor_row(executor))
                    existing_executor_ids.add(executor.executorId)

    def list_hub_mappings(
        self,
        local_type: str | None = None,
        local_id: str | None = None,
        hub_type: str | None = None,
        limit: int = 200,
    ) -> list[HubIdMapping]:
        query = select(HubIdMappingRecord).where(HubIdMappingRecord.workspace_id == self.workspace_id)
        if local_type:
            query = query.where(HubIdMappingRecord.local_type == local_type)
        if local_id:
            query = query.where(HubIdMappingRecord.local_id == local_id)
        if hub_type:
            query = query.where(HubIdMappingRecord.hub_type == hub_type)
        with self.database.session() as session:
            rows = session.scalars(query.order_by(HubIdMappingRecord.updated_at.desc()).limit(limit)).all()
            return [self._hub_mapping_model(row) for row in rows]

    def get_hub_mapping(
        self,
        local_type: str,
        local_id: str,
        hub_type: str | None = None,
    ) -> HubIdMapping | None:
        query = select(HubIdMappingRecord).where(
            HubIdMappingRecord.workspace_id == self.workspace_id,
            HubIdMappingRecord.local_type == local_type,
            HubIdMappingRecord.local_id == local_id,
        )
        if hub_type:
            query = query.where(HubIdMappingRecord.hub_type == hub_type)
        with self.database.session() as session:
            row = session.scalar(query.order_by(HubIdMappingRecord.updated_at.desc()).limit(1))
            return self._hub_mapping_model(row) if row else None

    def get_hub_mapping_by_hub_id(self, hub_type: str, hub_id: str) -> HubIdMapping | None:
        with self.database.session() as session:
            row = session.scalar(
                select(HubIdMappingRecord)
                .where(
                    HubIdMappingRecord.workspace_id == self.workspace_id,
                    HubIdMappingRecord.hub_type == hub_type,
                    HubIdMappingRecord.hub_id == hub_id,
                )
                .order_by(HubIdMappingRecord.updated_at.desc())
                .limit(1)
            )
            return self._hub_mapping_model(row) if row else None

    def upsert_hub_mapping(
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
        now = now_utc()
        with self.database.session() as session:
            row = session.scalar(
                select(HubIdMappingRecord).where(
                    HubIdMappingRecord.workspace_id == self.workspace_id,
                    HubIdMappingRecord.local_type == local_type,
                    HubIdMappingRecord.local_id == local_id,
                    HubIdMappingRecord.hub_type == hub_type,
                )
            )
            if row is None:
                row = HubIdMappingRecord(
                    workspace_id=self.workspace_id,
                    local_type=local_type,
                    local_id=local_id,
                    hub_type=hub_type,
                    hub_id=hub_id,
                    external_id=external_id,
                    external_trace_id=external_trace_id,
                    hub_trace_id=hub_trace_id,
                    sync_status=sync_status,
                    last_error=last_error,
                    metadata_json=metadata or {},
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.hub_id = hub_id
                row.external_id = external_id
                row.external_trace_id = external_trace_id
                row.hub_trace_id = hub_trace_id
                row.sync_status = sync_status
                row.last_error = last_error
                row.metadata_json = metadata or {}
                row.updated_at = now
        mapping = self.get_hub_mapping(local_type, local_id, hub_type)
        if mapping is None:
            raise RuntimeError("hub mapping was not persisted")
        return mapping

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
            return SiteMap.model_validate(map_with_default_path_groups(row.map_json))

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
            return SiteMap.model_validate(map_with_default_path_groups(row.map_json)) if row else None

    def publish_draft(self, draft_id: str) -> tuple[SiteMap, dict[str, int]] | None:
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
            sync_summary = self._sync_map_targets(session, map_json)
            draft.status = "published"
            draft.published_at = now_utc()
            self._add_audit(
                session,
                "map.draft.published",
                "map",
                draft.map_id,
                after={"draftId": draft_id, "configVersion": map_json["configVersion"], "targetSync": sync_summary},
            )
            return SiteMap.model_validate(map_json), sync_summary

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

    def create_robot(self, robot: RobotState) -> RobotState:
        with self.database.session() as session:
            existing = session.scalar(
                select(RobotInstanceRecord.id).where(
                    RobotInstanceRecord.workspace_id == self.workspace_id,
                    RobotInstanceRecord.robot_code == robot.robotId,
                )
            )
            if existing is not None:
                raise ValueError(f"robotCode already exists: {robot.robotId}")
            session.add(self._robot_row(robot))
            config_exists = session.scalar(
                select(RobotConfigRecord.id).where(
                    RobotConfigRecord.workspace_id == self.workspace_id,
                    RobotConfigRecord.robot_code == robot.robotId,
                )
            )
            if config_exists is None:
                session.add(
                    self._robot_config_row(
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
                        )
                    )
                )
            self._add_audit(
                session,
                "robot.created",
                "robot",
                robot.robotId,
                after=robot.model_dump(),
            )
        self.runtime_cache.set_robot(robot)
        return robot

    def list_targets(self, target_type: str | None = None, status: str | None = None) -> list[TargetRegistryItem]:
        query = select(TargetRegistryRecord).where(TargetRegistryRecord.workspace_id == self.workspace_id)
        if target_type:
            query = query.where(TargetRegistryRecord.target_type == target_type)
        if status:
            query = query.where(TargetRegistryRecord.status == status)
        with self.database.session() as session:
            rows = session.scalars(query.order_by(TargetRegistryRecord.target_type, TargetRegistryRecord.target_id)).all()
            return [self._target_model(row) for row in rows]

    def get_target(self, target_id: str) -> TargetRegistryItem | None:
        with self.database.session() as session:
            row = session.scalar(
                select(TargetRegistryRecord).where(
                    TargetRegistryRecord.workspace_id == self.workspace_id,
                    TargetRegistryRecord.target_id == target_id,
                )
            )
            return self._target_model(row) if row else None

    def create_target(self, request: TargetRegistryItemCreate) -> TargetRegistryItem:
        now = now_utc()
        target = TargetRegistryItem(**request.model_dump(), createdAt=iso_datetime(now), updatedAt=iso_datetime(now))
        with self.database.session() as session:
            existing = session.scalar(
                select(TargetRegistryRecord.id).where(
                    TargetRegistryRecord.workspace_id == self.workspace_id,
                    TargetRegistryRecord.target_id == target.targetId,
                )
            )
            if existing is not None:
                raise ValueError(f"targetId already exists: {target.targetId}")
            session.add(self._target_row(target))
            self._add_audit(session, "target.created", "target", target.targetId, after=target.model_dump())
        return target

    def update_target(self, target_id: str, request: TargetRegistryItemUpdate) -> TargetRegistryItem | None:
        now = now_utc()
        with self.database.session() as session:
            row = session.scalar(
                select(TargetRegistryRecord).where(
                    TargetRegistryRecord.workspace_id == self.workspace_id,
                    TargetRegistryRecord.target_id == target_id,
                )
            )
            if row is None:
                return None
            before = self._target_model(row).model_dump()
            patch = request.model_dump(exclude_unset=True)
            for key, value in patch.items():
                if key == "displayName":
                    row.display_name = value
                elif key == "pose":
                    row.pose_json = value
                elif key == "geometryRef":
                    row.geometry_ref = value
                elif key == "metadata":
                    row.metadata_json = value
                elif key == "status":
                    row.status = value
                elif key == "version":
                    row.version = value
            row.updated_at = now
            updated = self._target_model(row)
            self._add_audit(session, "target.updated", "target", target_id, before=before, after=updated.model_dump())
            return updated

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

    def list_robot_configs(self) -> list[RobotConfig]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RobotConfigRecord)
                .where(RobotConfigRecord.workspace_id == self.workspace_id)
                .order_by(RobotConfigRecord.robot_code)
            ).all()
            return [self._robot_config_model(row) for row in rows]

    def get_robot_config(self, robot_code: str) -> RobotConfig | None:
        with self.database.session() as session:
            row = session.scalar(
                select(RobotConfigRecord).where(
                    RobotConfigRecord.workspace_id == self.workspace_id,
                    RobotConfigRecord.robot_code == robot_code,
                )
            )
            return self._robot_config_model(row) if row else None

    def create_robot_config(self, request: RobotConfigCreate) -> RobotConfig:
        now = now_utc()
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
        config = RobotConfig(**request.model_dump(), executorId=executor_id, executorStatus=executor_status, createdAt=iso_datetime(now), updatedAt=iso_datetime(now))
        with self.database.session() as session:
            existing = session.scalar(
                select(RobotConfigRecord.id).where(
                    RobotConfigRecord.workspace_id == self.workspace_id,
                    RobotConfigRecord.robot_code == request.robotCode,
                )
            )
            if existing is not None:
                raise ValueError(f"robotCode already exists: {request.robotCode}")
            session.add(self._robot_config_row(config))
            robot_exists = session.scalar(
                select(RobotInstanceRecord.id).where(
                    RobotInstanceRecord.workspace_id == self.workspace_id,
                    RobotInstanceRecord.robot_code == request.robotCode,
                )
            )
            if robot_exists is None:
                session.add(
                    self._robot_row(
                        RobotState(
                            robotId=request.robotCode,
                            robotType=request.robotType,
                            state="Idle" if request.enabled else "Disabled",
                            x=request.initialPose.x,
                            y=request.initialPose.y,
                            progress=0,
                            currentAction="Waiting for command",
                            updatedAt=iso_datetime(now),
                        )
                    )
                )
            self._add_audit(session, "robot.config.created", "robot", request.robotCode, after=config.model_dump())
        return config

    def update_robot_config(self, robot_code: str, request: RobotConfigUpdate) -> RobotConfig | None:
        now = now_utc()
        with self.database.session() as session:
            row = session.scalar(
                select(RobotConfigRecord).where(
                    RobotConfigRecord.workspace_id == self.workspace_id,
                    RobotConfigRecord.robot_code == robot_code,
                )
            )
            if row is None:
                return None
            before = self._robot_config_model(row).model_dump()
            patch = request.model_dump(exclude_unset=True)
            for key, value in patch.items():
                if key == "robotName":
                    row.robot_name = value
                elif key == "robotType":
                    row.robot_type = value
                elif key == "status":
                    row.status = value
                elif key == "enabled":
                    row.enabled = value
                elif key == "capabilities":
                    row.capabilities_json = value
                elif key == "actionSetId":
                    row.action_set_id = value
                elif key == "mapId":
                    row.map_id = value
                elif key == "initialPose":
                    row.initial_pose_json = value
                elif key == "metadata":
                    row.metadata_json = value
            row.updated_at = now
            updated = self._robot_config_model(row)
            self._add_audit(session, "robot.config.updated", "robot", robot_code, before=before, after=updated.model_dump())
            return updated

    def delete_robot_config(self, robot_code: str) -> RobotConfig | None:
        return self.update_robot_config(robot_code, RobotConfigUpdate(status="deleted", enabled=False))

    def list_executors(self, robot_code: str | None = None) -> list[ExecutorInstance]:
        query = select(ExecutorInstanceRecord).where(ExecutorInstanceRecord.workspace_id == self.workspace_id)
        if robot_code:
            query = query.where(ExecutorInstanceRecord.robot_code == robot_code)
        with self.database.session() as session:
            rows = session.scalars(query.order_by(ExecutorInstanceRecord.robot_code, ExecutorInstanceRecord.updated_at.desc())).all()
            return [self._executor_model(row) for row in rows]

    def get_executor(self, executor_id: str) -> ExecutorInstance | None:
        with self.database.session() as session:
            row = session.scalar(
                select(ExecutorInstanceRecord).where(
                    ExecutorInstanceRecord.workspace_id == self.workspace_id,
                    ExecutorInstanceRecord.executor_id == executor_id,
                )
            )
            return self._executor_model(row) if row else None

    def create_executor(self, request: ExecutorInstanceCreate) -> ExecutorInstance:
        now = now_utc()
        executor = ExecutorInstance(
            executorId=new_id("exec"),
            robotCode=request.robotCode,
            executorType=request.executorType,
            status="active" if request.executorType == "virtual" else "binding",
            mqttClientId=request.mqttClientId or f"{request.executorType}-{request.robotCode}",
            containerName=request.containerName or (f"virtual-robot-runner-{request.robotCode}" if request.executorType == "virtual" else None),
            gatewayEndpoint=request.gatewayEndpoint,
            startedAt=iso_datetime(now),
            updatedAt=iso_datetime(now),
            metadata={
                "ROBOT_CODE": request.robotCode,
                "ROBOT_TYPE": request.robotType,
                "START_X": request.startPose.x if request.startPose else None,
                "START_Y": request.startPose.y if request.startPose else None,
                **request.metadata,
            },
        )
        with self.database.session() as session:
            active = session.scalar(
                select(ExecutorInstanceRecord).where(
                    ExecutorInstanceRecord.workspace_id == self.workspace_id,
                    ExecutorInstanceRecord.robot_code == request.robotCode,
                    ExecutorInstanceRecord.status == "active",
                )
            )
            if active is not None:
                raise ValueError(f"robot {request.robotCode} already has active executor {active.executor_id}")
            session.add(self._executor_row(executor))
            config = session.scalar(
                select(RobotConfigRecord).where(
                    RobotConfigRecord.workspace_id == self.workspace_id,
                    RobotConfigRecord.robot_code == request.robotCode,
                )
            )
            if config:
                config.executor_id = executor.executorId
                config.updated_at = now
            self._add_audit(session, "executor.created", "executor", executor.executorId, after=executor.model_dump())
        return executor

    def transition_executor(self, executor_id: str, status: str) -> ExecutorInstance | None:
        now = now_utc()
        with self.database.session() as session:
            row = session.scalar(
                select(ExecutorInstanceRecord).where(
                    ExecutorInstanceRecord.workspace_id == self.workspace_id,
                    ExecutorInstanceRecord.executor_id == executor_id,
                )
            )
            if row is None:
                return None
            before = self._executor_model(row).model_dump()
            row.status = status
            row.updated_at = now
            if status == "active" and row.started_at is None:
                row.started_at = now
            updated = self._executor_model(row)
            self._add_audit(session, f"executor.{status}", "executor", executor_id, before=before, after=updated.model_dump())
            return updated

    def executor_logs(self, executor_id: str, limit: int = 100) -> list[MessageRecord]:
        executor = self.get_executor(executor_id)
        if executor is None:
            return []
        return self.query_messages(limit=limit, robot_code=executor.robotCode)

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

    def validate_scenario(self, scenario_id: str) -> ScenarioValidationResponse | None:
        scenario = self.get_scenario(scenario_id)
        if scenario is None:
            return None

        checks: list[ScenarioValidationCheck] = []
        issues: list[str] = []

        def add_check(code: str, label: str, passed: bool, detail: str, warning: bool = False) -> None:
            status = "passed" if passed else ("warning" if warning else "failed")
            checks.append(ScenarioValidationCheck(code=code, label=label, status=status, detail=detail))
            if not passed and not warning:
                issues.append(detail)

        map_issues = self.validate_map(scenario.map)
        add_check(
            "map.integrity",
            "Map integrity",
            len(map_issues) == 0,
            "map validation passed" if not map_issues else "; ".join(map_issues[:3]),
        )
        target_sync_issues = target_registry_sync_issues(scenario.map, self.list_targets(status=None))
        add_check(
            "target.registry.sync",
            "Target Registry sync",
            len(target_sync_issues) == 0,
            "target registry matches current map" if not target_sync_issues else "; ".join(target_sync_issues[:3]),
        )

        stations = [item for item in scenario.map.objects if item.type == "station"]
        path_nodes = [item for item in scenario.map.objects if item.type == "pathNode"]
        add_check("station.exists", "Stations", len(stations) > 0, "at least one station is required")
        add_check("path.exists", "Path graph", len(path_nodes) > 1 and len(scenario.map.pathEdges) > 0, "path graph must contain nodes and edges")
        add_check("robot.exists", "Robot instances", len(scenario.robotCodes) > 0, "at least one robot instance is required")

        commands = set(scenario.actionSet.get("commands") or [])
        add_check(
            "actionset.commands",
            "Action set",
            {"goto_pose", "where", "stop"}.issubset(commands),
            "action set must support goto_pose, where and stop",
        )

        templates = self.list_task_templates()
        add_check("task.templates", "Task templates", len(templates) > 0, "at least one task template is required")

        resource_profile = scenario.resourceProfile or {}
        add_check(
            "resource.profile",
            "Resource profile",
            "pathCapacity" in resource_profile,
            "resource profile should define pathCapacity",
            warning=True,
        )

        task_modes = set((scenario.taskFlow or {}).get("modes") or [])
        add_check(
            "taskflow.modes",
            "Task flow modes",
            {"manual", "template"}.issubset(task_modes),
            "task flow should support manual and template creation",
            warning=True,
        )

        return ScenarioValidationResponse(
            scenarioId=scenario.scenarioId,
            ok=not issues,
            issues=issues,
            checks=checks,
        )

    def list_task_templates(self) -> list[TaskTemplate]:
        return self.TASK_TEMPLATES

    def create_simulation_run(self, request: SimulationRunCreate) -> SimulationRun:
        scenario = self.get_scenario(request.scenarioId)
        if scenario is None:
            raise ValueError("scenario not found")
        run_id = request.runId or protocol_id("RUN")
        created_at = now_utc()
        scenario_json = scenario.model_dump(by_alias=True)
        with self.database.session() as session:
            if self._run_row(session, run_id) is not None:
                raise ValueError(f"runId already exists: {run_id}")
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

    def create_batch_tasks(self, run_id: str, request: BatchTaskCreate) -> BatchTaskResponse | None:
        if self.get_simulation_run(run_id) is None:
            return None

        rng = random.Random(request.randomSeed)
        batch_id = protocol_id("BATCH")
        created_tasks: list[SimulationTask] = []
        target_range = request.targetRange or {}
        x_values = target_range.get("x") if isinstance(target_range.get("x"), list) else None
        y_values = target_range.get("y") if isinstance(target_range.get("y"), list) else None

        for index in range(request.count):
            parameters = {
                **request.parameters,
                "priority": request.priority,
                "constraints": {
                    **dict(request.parameters.get("constraints") or {}),
                    "batchId": batch_id,
                    "batchIndex": index + 1,
                    "intervalMs": request.intervalMs,
                    "randomizeRobot": request.randomizeRobot,
                    "randomizeTaskType": request.randomizeTaskType,
                    "autoRun": request.autoRun,
                },
            }
            if x_values and y_values and len(x_values) == 2 and len(y_values) == 2:
                parameters["target"] = {
                    "x": rng.uniform(float(x_values[0]), float(x_values[1])),
                    "y": rng.uniform(float(y_values[0]), float(y_values[1])),
                    "z": 0,
                    "yaw": 0,
                }

            if request.templateId:
                task = self.create_task_from_template(
                    run_id,
                    TaskFromTemplateCreate(
                        templateId=request.templateId,
                        parameters={"goal": f"{request.goal} #{index + 1}", **parameters},
                        createdBy=request.createdBy,
                    ),
                )
            else:
                target = parameters.get("target") or {"x": 760, "y": 420, "z": 0, "yaw": 0}
                task = self.create_simulation_task(
                    run_id,
                    SimulationTaskCreate(
                        goal=f"{request.goal} #{index + 1}",
                        input={"command": "goto_pose", "target": target, "batchId": batch_id},
                        constraints=dict(parameters.get("constraints") or {}),
                        priority=request.priority,
                        expectedOutcome="batch simulation task",
                        createdBy=request.createdBy,
                    ),
                )
            if task:
                created_tasks.append(task)

        return BatchTaskResponse(
            batchId=batch_id,
            runId=run_id,
            requestedCount=request.count,
            createdCount=len(created_tasks),
            tasks=created_tasks,
        )

    def create_task_chain(self, run_id: str, request: TaskChainCreate) -> TaskChain | None:
        if self.get_simulation_run(run_id) is None:
            return None

        chain_id = protocol_id("CHAIN")
        now = now_utc()
        created_tasks: list[SimulationTask] = []
        for index, item in enumerate(request.tasks, start=1):
            constraints = {
                **item.constraints,
                "chainId": chain_id,
                "chainIndex": index,
                "chainMode": request.mode,
                "dependsOn": item.dependsOn,
                "triggerCondition": item.triggerCondition,
                "robotStrategy": request.robotStrategy,
                "failurePolicy": request.failurePolicy,
            }
            task = self.create_simulation_task(
                run_id,
                SimulationTaskCreate(
                    goal=item.goal,
                    input={**item.input, "chainId": chain_id, "chainIndex": index},
                    constraints=constraints,
                    priority=item.priority if item.priority is not None else request.priority,
                    expectedOutcome=item.expectedOutcome,
                    createdBy=request.createdBy,
                ),
            )
            if task is None:
                return None
            created_tasks.append(task)

        with self.database.session() as session:
            row = SimulationTaskChainRecord(
                workspace_id=self.workspace_id,
                run_id=run_id,
                chain_id=chain_id,
                name=request.name,
                description=request.description,
                mode=request.mode,
                trigger_policy=request.triggerPolicy,
                robot_strategy=request.robotStrategy,
                failure_policy=request.failurePolicy,
                priority=request.priority,
                status="Ready",
                metadata_json=request.metadata,
                created_by=request.createdBy,
                created_at=now,
            )
            session.add(row)
            for index, task in enumerate(created_tasks, start=1):
                item = request.tasks[index - 1]
                session.add(
                    SimulationTaskChainItemRecord(
                        workspace_id=self.workspace_id,
                        run_id=run_id,
                        chain_id=chain_id,
                        task_id=task.taskId,
                        sequence=index,
                        depends_on_json=item.dependsOn,
                        trigger_condition=item.triggerCondition,
                        status="Ready" if index == 1 or request.mode == "parallel" else "Waiting",
                        metadata_json=item.metadata,
                    )
                )
            self._add_trace_span(
                session,
                run_id=run_id,
                task_id=None,
                trace_id=run_id,
                entity_type="TaskChain",
                entity_id=chain_id,
                operation="task_chain.created",
                status="Completed",
                started_at=now,
                output_ref=f"tasks:{len(created_tasks)}",
            )
            self._refresh_current_state(session, run_id, None, now)
        return self.get_task_chain(chain_id)

    def list_task_chains(self, run_id: str) -> list[TaskChain]:
        with self.database.session() as session:
            rows = session.scalars(
                select(SimulationTaskChainRecord)
                .where(
                    SimulationTaskChainRecord.workspace_id == self.workspace_id,
                    SimulationTaskChainRecord.run_id == run_id,
                )
                .order_by(SimulationTaskChainRecord.created_at.asc())
            ).all()
            return [self._task_chain_model(session, row) for row in rows]

    def get_task_chain(self, chain_id: str) -> TaskChain | None:
        with self.database.session() as session:
            row = session.scalar(
                select(SimulationTaskChainRecord).where(
                    SimulationTaskChainRecord.workspace_id == self.workspace_id,
                    SimulationTaskChainRecord.chain_id == chain_id,
                )
            )
            return self._task_chain_model(session, row) if row else None

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

    def get_plan(self, plan_id: str) -> SimulationPlan | None:
        with self.database.session() as session:
            row = session.scalar(
                select(SimulationPlanRecord).where(
                    SimulationPlanRecord.workspace_id == self.workspace_id,
                    SimulationPlanRecord.plan_id == plan_id,
                )
            )
            return self._plan_model(row) if row else None

    def create_task_plan(self, task_id: str, request: SimulationPlanCreate) -> SimulationPlan | None:
        now = now_utc()
        with self.database.session() as session:
            task = session.scalar(
                select(SimulationTaskRecord).where(
                    SimulationTaskRecord.workspace_id == self.workspace_id,
                    SimulationTaskRecord.task_id == task_id,
                )
            )
            if task is None:
                return None
            latest_version = session.scalar(
                select(func.max(SimulationPlanRecord.plan_version)).where(
                    SimulationPlanRecord.workspace_id == self.workspace_id,
                    SimulationPlanRecord.task_id == task_id,
                )
            ) or 0
            if request.activate:
                existing_active = session.scalars(
                    select(SimulationPlanRecord).where(
                        SimulationPlanRecord.workspace_id == self.workspace_id,
                        SimulationPlanRecord.task_id == task_id,
                        SimulationPlanRecord.status == "Active",
                    )
                ).all()
                for active in existing_active:
                    active.status = "Superseded"
            plan_id = protocol_id("PLAN")
            steps: list[PlanStep] = []
            for index, step in enumerate(request.steps, start=1):
                steps.append(
                    PlanStep(
                        planStepId=protocol_id("STEP"),
                        sequence=index,
                        actionType=step.actionType,
                        target=step.target,
                        params=step.params,
                        dependsOn=step.dependsOn,
                        successCondition=step.successCondition,
                        failurePolicy=step.failurePolicy,
                        timeoutMs=step.timeoutMs,
                        status=step.status,
                    )
                )
            row = SimulationPlanRecord(
                workspace_id=self.workspace_id,
                run_id=task.run_id,
                task_id=task.task_id,
                plan_id=plan_id,
                trace_id=task.trace_id,
                plan_version=int(latest_version) + 1,
                strategy=request.strategy,
                steps_json=[step.model_dump() for step in steps],
                dependencies_json=request.dependencies,
                assumptions_json=request.assumptions,
                generated_by=request.generatedBy,
                generation_latency_ms=0,
                status="Active" if request.activate else "Ready",
                created_at=now,
                activated_at=now if request.activate else None,
            )
            session.add(row)
            for step in steps:
                session.add(
                    SimulationPlanStepRecord(
                        workspace_id=self.workspace_id,
                        run_id=task.run_id,
                        task_id=task.task_id,
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
            self._add_trace_span(
                session,
                run_id=task.run_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                entity_type="Plan",
                entity_id=plan_id,
                operation="plan.created",
                status="Completed",
                started_at=now,
                output_ref=f"plan:{plan_id}",
            )
            self._refresh_current_state(session, task.run_id, None, now)
        plans = self.list_task_plans(task_id)
        return next((plan for plan in plans if plan.planId == plan_id), None)

    def create_action(self, request: ActionCreate) -> SimulationAction | None:
        now = now_utc()
        action_id = protocol_id("ACTION")
        robot_code = request.robotCode or self._default_robot_code()
        params = self.resolve_action_params(request.command, request.params)
        with self.database.session() as session:
            robot_exists = session.scalar(
                select(func.count()).select_from(RobotInstanceRecord).where(
                    RobotInstanceRecord.workspace_id == self.workspace_id,
                    RobotInstanceRecord.robot_code == robot_code,
                )
            )
            if not robot_exists:
                raise ValueError(f"unknown robotCode: {robot_code}")
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
                params_json=params,
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
            if row.plan_id and row.plan_step_id:
                self._update_plan_step_status(session, row.plan_id, row.plan_step_id, "Issued")
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

    def _update_plan_step_status(self, session: Any, plan_id: str, plan_step_id: str, status: str) -> None:
        step_row = session.scalar(
            select(SimulationPlanStepRecord).where(
                SimulationPlanStepRecord.workspace_id == self.workspace_id,
                SimulationPlanStepRecord.plan_id == plan_id,
                SimulationPlanStepRecord.plan_step_id == plan_step_id,
            )
        )
        if step_row is not None:
            step_row.status = status
        plan_row = session.scalar(
            select(SimulationPlanRecord).where(
                SimulationPlanRecord.workspace_id == self.workspace_id,
                SimulationPlanRecord.plan_id == plan_id,
            )
        )
        if plan_row is not None:
            next_steps: list[dict[str, Any]] = []
            for step in plan_row.steps_json:
                if step.get("planStepId") == plan_step_id:
                    next_steps.append({**step, "status": status})
                else:
                    next_steps.append(step)
            plan_row.steps_json = next_steps

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
        if request.eventType == "robot.offline" and request.targetType == "robot" and request.targetId:
            robots = {robot.robotId: robot for robot in self.robots()}
            robot = robots.get(request.targetId)
            if robot:
                self.upsert_robot_state(
                    RobotState(
                        robotId=robot.robotId,
                        robotType=robot.robotType,
                        state="Offline",
                        x=robot.x,
                        y=robot.y,
                        progress=robot.progress,
                        currentAction="fault.injected",
                        updatedAt=iso_datetime(timestamp),
                    )
                )
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

    def recover_simulation_event(
        self,
        run_id: str,
        request: SimulationEventRecoveryCreate,
    ) -> Observation | None:
        timestamp = now_utc()
        event_id = protocol_id("EVT")
        with self.database.session() as session:
            run = self._run_row(session, run_id)
            if run is None:
                return None
            active_task = self._latest_active_task(session, run_id)
            trace_id = active_task.trace_id if active_task else run_id

        if request.targetType == "robot" and request.targetId:
            robots = {robot.robotId: robot for robot in self.robots()}
            robot = robots.get(request.targetId)
            if robot and robot.state == "Offline":
                self.upsert_robot_state(
                    RobotState(
                        robotId=robot.robotId,
                        robotType=robot.robotType,
                        state="Idle",
                        x=robot.x,
                        y=robot.y,
                        progress=robot.progress,
                        currentAction="recovered",
                        updatedAt=iso_datetime(timestamp),
                    )
                )

        payload = {
            "schemaVersion": "1.0",
            "messageType": "event",
            "event": "fault.recovered",
            "eventId": event_id,
            "commandId": None,
            "taskId": active_task.task_id if active_task else None,
            "requestId": None,
            "robotCode": request.targetId if request.targetType == "robot" else None,
            "traceId": trace_id,
            "source": "simulation-console",
            "timestamp": iso_datetime(timestamp),
            "data": {
                "eventType": request.eventType,
                "targetType": request.targetType,
                "targetId": request.targetId,
                "recoveryMode": request.recoveryMode,
                "reason": request.reason,
                "operatorId": request.operatorId,
                "severity": "info",
            },
            "error": None,
        }
        message = MessageRecord(
            messageId=event_id,
            messageType="event",
            source="simulation-console",
            topic=f"simulation/runs/{run_id}/events/recovery",
            createdAt=payload["timestamp"],
            payload=payload,
        )
        self.append_message(message)
        observation = self.ingest_observation_from_message(message)
        self._clear_recovered_fault(run_id, request, observation)
        return observation

    def replay_run_message(
        self,
        run_id: str,
        message_id: str,
        request: MessageReplayCreate,
    ) -> MessageReplayResponse | None:
        original = next((message for message in self.list_run_messages(run_id, limit=1000) if message.messageId == message_id), None)
        if original is None:
            return None
        timestamp = utc_now()
        replay_id = protocol_id("REPLAY")
        original_payload = original.payload
        payload = {
            "schemaVersion": "1.0",
            "messageType": "event",
            "event": "message.replayed",
            "eventId": replay_id,
            "runId": run_id,
            "commandId": original_payload.get("commandId"),
            "taskId": original_payload.get("taskId"),
            "requestId": original_payload.get("requestId"),
            "robotCode": original_payload.get("robotCode") or original_payload.get("robotId"),
            "traceId": original_payload.get("traceId") or run_id,
            "source": "simulation-replay",
            "timestamp": timestamp,
            "data": {
                "replayMode": request.replayMode,
                "sandbox": request.sandbox,
                "reason": request.reason,
                "operatorId": request.operatorId,
                "replayOf": original.messageId,
                "originalMessage": original.model_dump(),
            },
            "error": None,
        }
        message = MessageRecord(
            messageId=replay_id,
            messageType="event",
            source="simulation-replay",
            topic=f"simulation/runs/{run_id}/messages/replay",
            createdAt=timestamp,
            payload=payload,
        )
        self.append_message(message)
        observation = self.ingest_observation_from_message(message)
        return MessageReplayResponse(
            replayId=replay_id,
            runId=run_id,
            replayMode=request.replayMode,
            sandbox=request.sandbox,
            message=message,
            observation=observation,
        )

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

    def get_task_trace(self, task_id: str) -> TraceResponse:
        task = self.get_task(task_id)
        if task is None:
            return TraceResponse(traceId=task_id, status="NotFound")
        return self.get_trace(task.traceId)

    def get_action_trace(self, action_id: str) -> TraceResponse:
        action = self.get_action(action_id)
        if action is None:
            return TraceResponse(traceId=action_id, status="NotFound")
        return self.get_trace(action.traceId)

    def get_trace_graph(self, trace_id: str) -> dict[str, Any]:
        trace = self.get_trace(trace_id)
        if trace.status == "NotFound":
            return {"traceId": trace_id, "status": "NotFound", "nodes": [], "edges": []}
        nodes = [
            {
                "id": span.spanId,
                "label": span.operation,
                "type": span.entityType,
                "entityId": span.entityId,
                "status": span.status,
                "startedAt": span.startedAt,
            }
            for span in trace.spans
        ]
        edges = []
        previous_id: str | None = None
        for span in trace.spans:
            if span.parentSpanId:
                edges.append({"from": span.parentSpanId, "to": span.spanId, "type": "parent"})
            elif previous_id:
                edges.append({"from": previous_id, "to": span.spanId, "type": "sequence"})
            previous_id = span.spanId
        return {"traceId": trace_id, "status": trace.status, "nodes": nodes, "edges": edges}

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

    def run_message_metrics(self, run_id: str) -> dict[str, Any]:
        messages = list(reversed(self.list_run_messages(run_id, limit=1000)))
        category_counts: dict[str, int] = {}
        event_counts: dict[str, int] = {}
        seen_ids: set[str] = set()
        duplicate_count = 0
        command_times: dict[str, datetime] = {}
        ack_delays: list[int] = []
        timeout_count = 0
        error_count = 0
        for message in messages:
            category = self._message_category(message)
            category_counts[category] = category_counts.get(category, 0) + 1
            event = str(message.payload.get("event") or message.payload.get("command") or message.messageType)
            event_counts[event] = event_counts.get(event, 0) + 1
            if message.messageId in seen_ids:
                duplicate_count += 1
            seen_ids.add(message.messageId)
            command_id = message.payload.get("commandId")
            if message.messageType == "command" and command_id:
                command_times[str(command_id)] = parse_datetime(message.createdAt)
            if event in {"command.accepted", "command.rejected"} and command_id and str(command_id) in command_times:
                ack_delays.append(int((parse_datetime(message.createdAt) - command_times[str(command_id)]).total_seconds() * 1000))
            if "timeout" in event:
                timeout_count += 1
            if category == "Alert" or message.payload.get("error"):
                error_count += 1
        return {
            "runId": run_id,
            "messageCount": len(messages),
            "categoryCounts": category_counts,
            "eventCounts": event_counts,
            "duplicateCount": duplicate_count,
            "timeoutCount": timeout_count,
            "errorCount": error_count,
            "ackDelayMs": {
                "count": len(ack_delays),
                "avg": round(sum(ack_delays) / len(ack_delays), 2) if ack_delays else None,
                "max": max(ack_delays) if ack_delays else None,
            },
        }

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
            robots=[
                {
                    "robotCode": robot.robotId,
                    "robotType": robot.robotType,
                    "initialPose": {"x": robot.x, "y": robot.y},
                    "state": robot.state,
                    "capabilities": action_command_names(),
                }
                for robot in robots
            ],
            robotTypeIds=sorted({robot.robotType for robot in robots}) or ["machine-dog"],
            actionSet={"actionSetId": "machine-dog-basic", "commands": action_command_names()},
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
        active = session.scalar(
            select(SimulationPlanRecord)
            .where(
                SimulationPlanRecord.workspace_id == self.workspace_id,
                SimulationPlanRecord.task_id == task_id,
                SimulationPlanRecord.status == "Active",
            )
            .order_by(SimulationPlanRecord.plan_version.desc())
            .limit(1)
        )
        if active is not None:
            return active
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

    def _task_chain_model(self, session: Any, row: SimulationTaskChainRecord) -> TaskChain:
        item_rows = session.scalars(
            select(SimulationTaskChainItemRecord)
            .where(
                SimulationTaskChainItemRecord.workspace_id == self.workspace_id,
                SimulationTaskChainItemRecord.chain_id == row.chain_id,
            )
            .order_by(SimulationTaskChainItemRecord.sequence.asc())
        ).all()
        task_ids = [item.task_id for item in item_rows]
        tasks_by_id: dict[str, SimulationTask] = {}
        if task_ids:
            task_rows = session.scalars(
                select(SimulationTaskRecord).where(
                    SimulationTaskRecord.workspace_id == self.workspace_id,
                    SimulationTaskRecord.task_id.in_(task_ids),
                )
            ).all()
            for task_row in task_rows:
                plan = self._active_plan_row(session, task_row.task_id)
                tasks_by_id[task_row.task_id] = self._task_model(task_row, self._plan_model(plan) if plan else None)
        return TaskChain(
            chainId=row.chain_id,
            runId=row.run_id,
            name=row.name,
            description=row.description,
            mode=row.mode,
            triggerPolicy=row.trigger_policy,
            robotStrategy=row.robot_strategy,
            failurePolicy=row.failure_policy,
            priority=row.priority,
            status=row.status,
            metadata=row.metadata_json,
            createdBy=row.created_by,
            createdAt=iso_datetime(row.created_at),
            startedAt=iso_datetime(row.started_at) if row.started_at else None,
            finishedAt=iso_datetime(row.finished_at) if row.finished_at else None,
            items=[
                TaskChainItem(
                    chainId=item.chain_id,
                    runId=item.run_id,
                    taskId=item.task_id,
                    sequence=item.sequence,
                    dependsOn=item.depends_on_json,
                    triggerCondition=item.trigger_condition,
                    status=item.status,
                    metadata=item.metadata_json,
                    task=tasks_by_id.get(item.task_id),
                )
                for item in item_rows
            ],
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

    def _sync_map_targets(self, session: Session, map_data: dict[str, Any] | SiteMap) -> dict[str, int]:
        now = now_utc()
        timestamp = iso_datetime(now)
        map_json = map_with_default_path_groups(map_data)
        map_id = str(map_json.get("id") or "site-a")
        desired_targets = [
            TargetRegistryItem.model_validate(target)
            for target in map_source_targets_from_map(map_json, timestamp)
        ]
        desired_ids = {target.targetId for target in desired_targets}
        rows = session.scalars(
            select(TargetRegistryRecord).where(TargetRegistryRecord.workspace_id == self.workspace_id)
        ).all()
        existing_by_id = {row.target_id: row for row in rows}
        summary = {"created": 0, "updated": 0, "inactivated": 0}

        for target in desired_targets:
            row = existing_by_id.get(target.targetId)
            target_pose = target.pose.model_dump() if target.pose else None
            if row is None:
                session.add(self._target_row(target))
                summary["created"] += 1
                continue
            changed = any(
                [
                    row.target_type != target.targetType,
                    row.display_name != target.displayName,
                    row.map_id != target.mapId,
                    row.pose_json != target_pose,
                    row.geometry_ref != target.geometryRef,
                    row.metadata_json != target.metadata,
                    row.status != target.status,
                    row.version != target.version,
                ]
            )
            row.target_type = target.targetType
            row.display_name = target.displayName
            row.map_id = target.mapId
            row.pose_json = target_pose
            row.geometry_ref = target.geometryRef
            row.metadata_json = target.metadata
            row.status = target.status
            row.version = target.version
            row.updated_at = now
            if changed:
                summary["updated"] += 1

        for row in rows:
            if row.target_id in desired_ids:
                continue
            if row.map_id != map_id or (row.metadata_json or {}).get("source") != "map":
                continue
            if row.status != "inactive":
                row.status = "inactive"
                row.updated_at = now
                summary["inactivated"] += 1

        return summary

    def _target_row(self, target: TargetRegistryItem) -> TargetRegistryRecord:
        return TargetRegistryRecord(
            workspace_id=self.workspace_id,
            target_id=target.targetId,
            target_type=target.targetType,
            display_name=target.displayName,
            map_id=target.mapId,
            pose_json=target.pose.model_dump() if target.pose else None,
            geometry_ref=target.geometryRef,
            metadata_json=target.metadata,
            status=target.status,
            version=target.version,
            created_at=parse_datetime(target.createdAt),
            updated_at=parse_datetime(target.updatedAt),
        )

    @staticmethod
    def _target_model(row: TargetRegistryRecord) -> TargetRegistryItem:
        return TargetRegistryItem(
            targetId=row.target_id,
            targetType=row.target_type,
            displayName=row.display_name,
            mapId=row.map_id,
            pose=row.pose_json,
            geometryRef=row.geometry_ref,
            metadata=row.metadata_json,
            status=row.status,
            version=row.version,
            createdAt=iso_datetime(row.created_at),
            updatedAt=iso_datetime(row.updated_at),
        )

    def _robot_config_row(self, config: RobotConfig) -> RobotConfigRecord:
        return RobotConfigRecord(
            workspace_id=self.workspace_id,
            robot_code=config.robotCode,
            robot_name=config.robotName,
            robot_type=config.robotType,
            status=config.status,
            enabled=config.enabled,
            capabilities_json=config.capabilities,
            action_set_id=config.actionSetId,
            map_id=config.mapId,
            initial_pose_json=config.initialPose.model_dump(),
            executor_id=config.executorId,
            metadata_json=config.metadata,
            created_at=parse_datetime(config.createdAt),
            updated_at=parse_datetime(config.updatedAt),
        )

    def _robot_config_model(self, row: RobotConfigRecord) -> RobotConfig:
        executor_status = None
        if row.executor_id:
            with self.database.session() as session:
                executor = session.scalar(
                    select(ExecutorInstanceRecord).where(
                        ExecutorInstanceRecord.workspace_id == self.workspace_id,
                        ExecutorInstanceRecord.executor_id == row.executor_id,
                    )
                )
                executor_status = executor.status if executor else None
        return RobotConfig(
            robotCode=row.robot_code,
            robotName=row.robot_name,
            robotType=row.robot_type,
            status=row.status,
            enabled=row.enabled,
            capabilities=row.capabilities_json,
            actionSetId=row.action_set_id,
            mapId=row.map_id,
            initialPose=row.initial_pose_json,
            createMode="config_only",
            executorEndpoint=None,
            metadata=row.metadata_json,
            executorId=row.executor_id,
            executorStatus=executor_status,
            createdAt=iso_datetime(row.created_at),
            updatedAt=iso_datetime(row.updated_at),
        )

    def _executor_row(self, executor: ExecutorInstance) -> ExecutorInstanceRecord:
        return ExecutorInstanceRecord(
            workspace_id=self.workspace_id,
            executor_id=executor.executorId,
            robot_code=executor.robotCode,
            executor_type=executor.executorType,
            status=executor.status,
            mqtt_client_id=executor.mqttClientId,
            last_heartbeat_at=parse_datetime(executor.lastHeartbeatAt) if executor.lastHeartbeatAt else None,
            container_name=executor.containerName,
            gateway_endpoint=executor.gatewayEndpoint,
            started_at=parse_datetime(executor.startedAt) if executor.startedAt else None,
            metadata_json=executor.metadata,
            updated_at=parse_datetime(executor.updatedAt),
        )

    @staticmethod
    def _executor_model(row: ExecutorInstanceRecord) -> ExecutorInstance:
        return ExecutorInstance(
            executorId=row.executor_id,
            robotCode=row.robot_code,
            executorType=row.executor_type,
            status=row.status,
            mqttClientId=row.mqtt_client_id,
            lastHeartbeatAt=iso_datetime(row.last_heartbeat_at) if row.last_heartbeat_at else None,
            containerName=row.container_name,
            gatewayEndpoint=row.gateway_endpoint,
            startedAt=iso_datetime(row.started_at) if row.started_at else None,
            updatedAt=iso_datetime(row.updated_at),
            metadata=row.metadata_json,
        )

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
        if event in {"pose.updated", "where.result", "action.progress"}:
            return "Telemetry"
        if event.startswith("interface."):
            return "Interface"
        if event.startswith("agent."):
            return "AgentDecision"
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
        elif event in {"task.started", "action.started", "action.progress"}:
            action.status = "Running"
            action.started_at = action.started_at or timestamp
        elif event in {"task.succeeded", "where.result", "action.succeeded"}:
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
        if event in {"task.started", "action.started", "action.progress"}:
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
            if observation.category in {"Alert", "Interface"}:
                active_events = [event_item, *active_events][:20]
                environment_state["alerts"] = active_events
            if observation.event == "path.blocked":
                blocked_paths = list(resource_states.get("blockedPaths") or [])
                target_id = observation.data_json.get("targetId")
                if target_id and target_id not in blocked_paths:
                    blocked_paths.append(target_id)
                resource_states["blockedPaths"] = blocked_paths
            if observation.event == "station.unavailable":
                stations = list(resource_states.get("stations") or [])
                target_id = observation.data_json.get("targetId")
                if target_id and target_id not in [item.get("stationId") for item in stations if isinstance(item, dict)]:
                    stations.append({"stationId": target_id, "state": "Unavailable", "timestamp": iso_datetime(timestamp)})
                resource_states["stations"] = stations[-20:]
            if observation.event == "resource.locked":
                locks = list(resource_states.get("locks") or [])
                locks.append({"resourceId": observation.data_json.get("targetId"), "timestamp": iso_datetime(timestamp)})
                resource_states["locks"] = locks[-20:]
            if observation.event in {"interface.timeout", "message.dropped"}:
                interface_events = list(environment_state.get("interfaceEvents") or [])
                interface_events.append(
                    {
                        "event": observation.event,
                        "targetId": observation.data_json.get("targetId"),
                        "timestamp": iso_datetime(timestamp),
                    }
                )
                environment_state["interfaceEvents"] = interface_events[-20:]
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

    def _clear_recovered_fault(
        self,
        run_id: str,
        request: SimulationEventRecoveryCreate,
        observation: Observation | None,
    ) -> None:
        timestamp = now_utc()
        with self.database.session() as session:
            row = self._current_state_row(session, run_id)
            if row is None:
                return
            target_id = request.targetId
            target_event = request.eventType
            active_events = [
                event
                for event in list(row.active_events_json or [])
                if not (
                    (not target_id or event.get("targetId") == target_id)
                    and (not target_event or event.get("event") == target_event)
                )
            ]
            resource_states = dict(row.resource_states_json or {})
            environment_state = dict(row.environment_state_json or {})
            if request.targetType == "path" and target_id:
                resource_states["blockedPaths"] = [
                    path_id for path_id in list(resource_states.get("blockedPaths") or []) if path_id != target_id
                ]
            if request.targetType == "resource" and target_id:
                resource_states["locks"] = [
                    lock
                    for lock in list(resource_states.get("locks") or [])
                    if not isinstance(lock, dict) or lock.get("resourceId") != target_id
                ]
            if request.targetType == "station" and target_id:
                resource_states["stations"] = [
                    station
                    for station in list(resource_states.get("stations") or [])
                    if not isinstance(station, dict) or station.get("stationId") != target_id
                ]
            environment_state["alerts"] = active_events
            row.active_events_json = active_events
            row.resource_states_json = resource_states
            row.environment_state_json = environment_state
            row.robot_states_json = [robot.model_dump() for robot in self.robots()]
            row.state_version += 1
            row.last_observation_id = observation.observationId if observation else row.last_observation_id
            row.last_observation_at = parse_datetime(observation.timestamp) if observation else row.last_observation_at
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

    @staticmethod
    def _hub_mapping_model(row: HubIdMappingRecord) -> HubIdMapping:
        return HubIdMapping(
            localType=row.local_type,
            localId=row.local_id,
            hubType=row.hub_type,
            hubId=row.hub_id,
            externalId=row.external_id,
            externalTraceId=row.external_trace_id,
            hubTraceId=row.hub_trace_id,
            syncStatus=row.sync_status,
            lastError=row.last_error,
            metadata=row.metadata_json,
            createdAt=iso_datetime(row.created_at),
            updatedAt=iso_datetime(row.updated_at),
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
            task_id=payload.get("taskId") or inner_payload.get("taskId"),
            request_id=payload.get("requestId") or inner_payload.get("requestId"),
            trace_id=payload.get("traceId") or inner_payload.get("traceId"),
            robot_code=payload.get("robotCode") or payload.get("robotId") or inner_payload.get("robotCode"),
            event=payload.get("event") or inner_payload.get("event") or inner_payload.get("eventType"),
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
