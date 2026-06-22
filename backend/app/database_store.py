from __future__ import annotations

import json
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
    SiteMapRecord,
    now_utc,
)
from .runtime_cache import RuntimeCache
from .schemas import MessageRecord, RobotState, SiteMap, new_id, utc_now
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
