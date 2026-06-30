from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, JSON, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


JSON_DOCUMENT = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class SiteMapRecord(Base):
    __tablename__ = "site_maps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "map_id", "config_version", name="uk_site_maps_version"),
        Index("idx_site_maps_active", "workspace_id", "map_id", "status"),
        {"schema": "config"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    map_id: Mapped[str] = mapped_column(String(128), nullable=False)
    map_name: Mapped[str] = mapped_column(String(256), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    map_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class MapDraftRecord(Base):
    __tablename__ = "map_drafts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "draft_id", name="uk_map_drafts_draft_id"),
        Index("idx_map_drafts_map", "workspace_id", "map_id", "created_at"),
        {"schema": "config"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    draft_id: Mapped[str] = mapped_column(String(128), nullable=False)
    map_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="editing")
    map_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TargetRegistryRecord(Base):
    __tablename__ = "target_registry"
    __table_args__ = (
        UniqueConstraint("workspace_id", "target_id", name="uk_target_registry_target_id"),
        Index("idx_target_registry_type", "workspace_id", "target_type", "status"),
        Index("idx_target_registry_map", "workspace_id", "map_id"),
        {"schema": "config"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    map_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pose_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    geometry_ref: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class RobotConfigRecord(Base):
    __tablename__ = "robot_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "robot_code", name="uk_robot_configs_code"),
        Index("idx_robot_configs_status", "workspace_id", "status", "enabled"),
        {"schema": "config"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    robot_code: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_name: Mapped[str | None] = mapped_column(String(256))
    robot_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="enabled")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False, default=list)
    action_set_id: Mapped[str] = mapped_column(String(128), nullable=False, default="machine-dog-basic")
    map_id: Mapped[str] = mapped_column(String(128), nullable=False, default="site-a")
    initial_pose_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    executor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class RobotInstanceRecord(Base):
    __tablename__ = "robot_instances"
    __table_args__ = (
        UniqueConstraint("workspace_id", "robot_code", name="uk_robot_instances_code"),
        {"schema": "runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    robot_code: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_type: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_state: Mapped[str] = mapped_column(String(32), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_action: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)


class ExecutorInstanceRecord(Base):
    __tablename__ = "executor_instances"
    __table_args__ = (
        UniqueConstraint("workspace_id", "executor_id", name="uk_executor_instances_executor_id"),
        Index("idx_executor_instances_robot", "workspace_id", "robot_code", "status"),
        {"schema": "runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    executor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    executor_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unbound")
    mqtt_client_id: Mapped[str] = mapped_column(String(256), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    container_name: Mapped[str | None] = mapped_column(String(256))
    gateway_endpoint: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class MessageRecordRow(Base):
    __tablename__ = "message_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "message_id", name="uk_message_records_message_id"),
        Index("idx_message_records_created", "workspace_id", "created_at"),
        Index("idx_message_records_type", "workspace_id", "message_type", "created_at"),
        Index("idx_message_records_topic", "workspace_id", "topic"),
        {"schema": "message"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    command_id: Mapped[str | None] = mapped_column(String(128), index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    robot_code: Mapped[str | None] = mapped_column(String(128), index=True)
    event: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_resource", "workspace_id", "resource_type", "resource_id"),
        {"schema": "audit"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    audit_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    actor_type: Mapped[str] = mapped_column(String(64), nullable=False, default="service")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)


class ExportJobRecord(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        Index("idx_export_jobs_created", "workspace_id", "created_at"),
        {"schema": "export"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    export_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    export_type: Mapped[str] = mapped_column(String(64), nullable=False)
    export_status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationRunRecord(Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uk_simulation_runs_run_id"),
        Index("idx_simulation_runs_status", "workspace_id", "status", "created_at"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Draft")
    map_id: Mapped[str] = mapped_column(String(128), nullable=False)
    map_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class SimulationTaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id", name="uk_simulation_tasks_task_id"),
        Index("idx_simulation_tasks_run", "workspace_id", "run_id", "created_at"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(String(512), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    expected_outcome: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Ready")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="simulation-console")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationPlanRecord(Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "plan_id", name="uk_simulation_plans_plan_id"),
        Index("idx_simulation_plans_task", "workspace_id", "task_id", "plan_version"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False, default="rule")
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    dependencies_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    assumptions_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    generated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="rule-agent")
    generation_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationPlanStepRecord(Base):
    __tablename__ = "plan_steps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "plan_step_id", name="uk_simulation_plan_steps_step_id"),
        Index("idx_simulation_plan_steps_plan", "workspace_id", "plan_id", "sequence"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plan_step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False)
    target_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    depends_on_json: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False, default=list)
    success_condition: Mapped[str | None] = mapped_column(String(512))
    failure_policy: Mapped[str] = mapped_column(String(128), nullable=False, default="surface_to_operator")
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=60000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Pending")


class SimulationTaskChainRecord(Base):
    __tablename__ = "task_chains"
    __table_args__ = (
        UniqueConstraint("workspace_id", "chain_id", name="uk_simulation_task_chains_chain_id"),
        Index("idx_simulation_task_chains_run", "workspace_id", "run_id", "created_at"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="serial")
    trigger_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="auto")
    robot_strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="specified")
    failure_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="stop_chain")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Ready")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="simulation-console")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationTaskChainItemRecord(Base):
    __tablename__ = "task_chain_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "chain_id", "sequence", name="uk_simulation_task_chain_items_sequence"),
        Index("idx_simulation_task_chain_items_chain", "workspace_id", "chain_id", "sequence"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    depends_on_json: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False, default=list)
    trigger_condition: Mapped[str] = mapped_column(String(128), nullable=False, default="previous_succeeded")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Pending")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class SimulationActionRecord(Base):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "action_id", name="uk_simulation_actions_action_id"),
        Index("idx_simulation_actions_run", "workspace_id", "run_id", "created_at"),
        Index("idx_simulation_actions_command", "workspace_id", "command_id"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(128), index=True)
    plan_step_id: Mapped[str | None] = mapped_column(String(128), index=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    robot_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    command: Mapped[str] = mapped_column(String(128), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    command_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=60000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Pending")
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationObservationRecord(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "observation_id", name="uk_simulation_observations_observation_id"),
        Index("idx_simulation_observations_run", "workspace_id", "run_id", "timestamp"),
        Index("idx_simulation_observations_command", "workspace_id", "command_id"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    action_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    observation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="Event")
    event_id: Mapped[str | None] = mapped_column(String(128), index=True)
    message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    robot_code: Mapped[str | None] = mapped_column(String(128), index=True)
    command_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="Applied")


class SimulationCurrentStateRecord(Base):
    __tablename__ = "current_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uk_simulation_current_states_run_id"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_state_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    active_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    robot_states_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    resource_states_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    environment_state_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    pending_actions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    active_events_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    last_observation_id: Mapped[str | None] = mapped_column(String(128))
    last_observation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)


class SimulationSnapshotRecord(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "snapshot_id", name="uk_simulation_snapshots_snapshot_id"),
        Index("idx_simulation_snapshots_run", "workspace_id", "run_id", "created_at"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)


class SimulationTraceHeaderRecord(Base):
    __tablename__ = "trace_headers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "trace_id", name="uk_simulation_trace_headers_trace_id"),
        Index("idx_simulation_trace_headers_run", "workspace_id", "run_id", "started_at"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Open")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class SimulationTraceSpanRecord(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "span_id", name="uk_simulation_trace_spans_span_id"),
        Index("idx_simulation_trace_spans_trace", "workspace_id", "trace_id", "started_at"),
        {"schema": "simulation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Completed")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_ref: Mapped[str | None] = mapped_column(String(256))
    output_ref: Mapped[str | None] = mapped_column(String(256))
    error_ref: Mapped[str | None] = mapped_column(String(256))


class HubIdMappingRecord(Base):
    __tablename__ = "hub_id_mappings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "local_type", "local_id", "hub_type", name="uk_hub_id_mappings_local"),
        Index("idx_hub_id_mappings_hub", "workspace_id", "hub_type", "hub_id"),
        Index("idx_hub_id_mappings_trace", "workspace_id", "external_trace_id", "hub_trace_id"),
        {"schema": "integration"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    local_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hub_type: Mapped[str] = mapped_column(String(64), nullable=False)
    hub_id: Mapped[str | None] = mapped_column(String(128), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    external_trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    hub_trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="synced")
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
