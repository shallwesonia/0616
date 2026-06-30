"""Add Hub integration ID mapping table.

Revision ID: 20260630_0005
Revises: 20260630_0004
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260630_0005"
down_revision: Union[str, Sequence[str], None] = "20260630_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "integration"'))
    op.create_table(
        "hub_id_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("local_type", sa.String(length=64), nullable=False),
        sa.Column("local_id", sa.String(length=128), nullable=False),
        sa.Column("hub_type", sa.String(length=64), nullable=False),
        sa.Column("hub_id", sa.String(length=128), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("external_trace_id", sa.String(length=128), nullable=True),
        sa.Column("hub_trace_id", sa.String(length=128), nullable=True),
        sa.Column("sync_status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata_json", jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "local_type", "local_id", "hub_type", name="uk_hub_id_mappings_local"),
        schema="integration",
    )
    op.create_index("idx_hub_id_mappings_hub", "hub_id_mappings", ["workspace_id", "hub_type", "hub_id"], schema="integration")
    op.create_index(
        "idx_hub_id_mappings_trace",
        "hub_id_mappings",
        ["workspace_id", "external_trace_id", "hub_trace_id"],
        schema="integration",
    )
    for column in ("workspace_id", "hub_id", "external_id", "external_trace_id", "hub_trace_id"):
        op.create_index(op.f(f"ix_integration_hub_id_mappings_{column}"), "hub_id_mappings", [column], schema="integration")


def downgrade() -> None:
    op.drop_table("hub_id_mappings", schema="integration")
