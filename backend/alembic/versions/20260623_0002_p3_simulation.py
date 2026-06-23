"""Create P3 simulation runtime tables.

Revision ID: 20260623_0002
Revises: 20260622_0001
Create Date: 2026-06-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260623_0002"
down_revision: Union[str, Sequence[str], None] = "20260622_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "simulation"'))

    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("scenario_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("map_id", sa.String(length=128), nullable=False),
        sa.Column("map_version", sa.String(length=64), nullable=False),
        sa.Column("scenario_json", jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "run_id", name="uk_simulation_runs_run_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_runs_status", "simulation_runs", ["workspace_id", "status", "created_at"], schema="simulation")
    op.create_index(op.f("ix_simulation_simulation_runs_workspace_id"), "simulation_runs", ["workspace_id"], schema="simulation")

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("goal", sa.String(length=512), nullable=False),
        sa.Column("input_json", jsonb(), nullable=False),
        sa.Column("constraints_json", jsonb(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("expected_outcome", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "task_id", name="uk_simulation_tasks_task_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_tasks_run", "tasks", ["workspace_id", "run_id", "created_at"], schema="simulation")
    for column in ("workspace_id", "run_id", "trace_id"):
        op.create_index(op.f(f"ix_simulation_tasks_{column}"), "tasks", [column], schema="simulation")

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=128), nullable=False),
        sa.Column("steps_json", jsonb(), nullable=False),
        sa.Column("dependencies_json", jsonb(), nullable=False),
        sa.Column("assumptions_json", jsonb(), nullable=False),
        sa.Column("generated_by", sa.String(length=128), nullable=False),
        sa.Column("generation_latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "plan_id", name="uk_simulation_plans_plan_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_plans_task", "plans", ["workspace_id", "task_id", "plan_version"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id", "trace_id"):
        op.create_index(op.f(f"ix_simulation_plans_{column}"), "plans", [column], schema="simulation")

    op.create_table(
        "plan_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.String(length=128), nullable=False),
        sa.Column("plan_step_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=128), nullable=False),
        sa.Column("target_json", jsonb(), nullable=False),
        sa.Column("params_json", jsonb(), nullable=False),
        sa.Column("depends_on_json", jsonb(), nullable=False),
        sa.Column("success_condition", sa.String(length=512), nullable=True),
        sa.Column("failure_policy", sa.String(length=128), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "plan_step_id", name="uk_simulation_plan_steps_step_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_plan_steps_plan", "plan_steps", ["workspace_id", "plan_id", "sequence"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id", "plan_id"):
        op.create_index(op.f(f"ix_simulation_plan_steps_{column}"), "plan_steps", [column], schema="simulation")

    op.create_table(
        "actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("plan_id", sa.String(length=128), nullable=True),
        sa.Column("plan_step_id", sa.String(length=128), nullable=True),
        sa.Column("action_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("robot_code", sa.String(length=128), nullable=False),
        sa.Column("command", sa.String(length=128), nullable=False),
        sa.Column("params_json", jsonb(), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", jsonb(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "action_id", name="uk_simulation_actions_action_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_actions_run", "actions", ["workspace_id", "run_id", "created_at"], schema="simulation")
    op.create_index("idx_simulation_actions_command", "actions", ["workspace_id", "command_id"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id", "plan_id", "plan_step_id", "trace_id", "robot_code", "command_id", "request_id"):
        op.create_index(op.f(f"ix_simulation_actions_{column}"), "actions", [column], schema="simulation")

    op.create_table(
        "observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("action_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("observation_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("robot_code", sa.String(length=128), nullable=True),
        sa.Column("command_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_json", jsonb(), nullable=False),
        sa.Column("error_json", jsonb(), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "observation_id", name="uk_simulation_observations_observation_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_observations_run", "observations", ["workspace_id", "run_id", "timestamp"], schema="simulation")
    op.create_index("idx_simulation_observations_command", "observations", ["workspace_id", "command_id"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id", "action_id", "trace_id", "event", "event_id", "message_id", "robot_code", "command_id", "request_id"):
        op.create_index(op.f(f"ix_simulation_observations_{column}"), "observations", [column], schema="simulation")

    op.create_table(
        "current_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("task_state_json", jsonb(), nullable=False),
        sa.Column("active_plan_json", jsonb(), nullable=True),
        sa.Column("robot_states_json", jsonb(), nullable=False),
        sa.Column("resource_states_json", jsonb(), nullable=False),
        sa.Column("environment_state_json", jsonb(), nullable=False),
        sa.Column("pending_actions_json", jsonb(), nullable=False),
        sa.Column("active_events_json", jsonb(), nullable=False),
        sa.Column("last_observation_id", sa.String(length=128), nullable=True),
        sa.Column("last_observation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "run_id", name="uk_simulation_current_states_run_id"),
        schema="simulation",
    )
    op.create_index(op.f("ix_simulation_current_states_workspace_id"), "current_states", ["workspace_id"], schema="simulation")

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("snapshot_json", jsonb(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "snapshot_id", name="uk_simulation_snapshots_snapshot_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_snapshots_run", "snapshots", ["workspace_id", "run_id", "created_at"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id", "trace_id"):
        op.create_index(op.f(f"ix_simulation_snapshots_{column}"), "snapshots", [column], schema="simulation")

    op.create_table(
        "trace_headers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "trace_id", name="uk_simulation_trace_headers_trace_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_trace_headers_run", "trace_headers", ["workspace_id", "run_id", "started_at"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id"):
        op.create_index(op.f(f"ix_simulation_trace_headers_{column}"), "trace_headers", [column], schema="simulation")

    op.create_table(
        "trace_spans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("span_id", sa.String(length=128), nullable=False),
        sa.Column("parent_span_id", sa.String(length=128), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_ref", sa.String(length=256), nullable=True),
        sa.Column("output_ref", sa.String(length=256), nullable=True),
        sa.Column("error_ref", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "span_id", name="uk_simulation_trace_spans_span_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_trace_spans_trace", "trace_spans", ["workspace_id", "trace_id", "started_at"], schema="simulation")
    for column in ("workspace_id", "run_id", "task_id", "trace_id", "parent_span_id"):
        op.create_index(op.f(f"ix_simulation_trace_spans_{column}"), "trace_spans", [column], schema="simulation")


def downgrade() -> None:
    for table_name in (
        "trace_spans",
        "trace_headers",
        "snapshots",
        "current_states",
        "observations",
        "actions",
        "plan_steps",
        "plans",
        "tasks",
        "simulation_runs",
    ):
        op.drop_table(table_name, schema="simulation")
    op.execute(sa.text('DROP SCHEMA IF EXISTS "simulation"'))
