"""Initial pocage control-plane foundation.

Revision ID: 20260412_0001
Revises:
Create Date: 2026-04-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260412_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=128), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prefix", sa.String(length=24), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_hash"),
    )
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"], unique=False)

    op.create_table(
        "machines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("arch", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_machines_user_id", "machines", ["user_id"], unique=False)

    op.create_table(
        "agent_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("machine_id", sa.Uuid(), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("daemon_id", sa.String(length=255), nullable=False),
        sa.Column("executor_name", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("workspace_roots", sa.JSON(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_instances_machine_id", "agent_instances", ["machine_id"], unique=False)
    op.create_index("ix_agent_instances_daemon_id", "agent_instances", ["daemon_id"], unique=False)

    op.create_table(
        "machine_pairings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("requested_agent", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_machine_pairings_user_id", "machine_pairings", ["user_id"], unique=False)

    op.create_table(
        "machine_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["agent_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_machine_credentials_agent_instance_id", "machine_credentials", ["agent_instance_id"], unique=False)

    op.create_table(
        "session_pointers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=False),
        sa.Column("remote_session_id", sa.String(length=255), nullable=False),
        sa.Column("title_hint", sa.String(length=255), nullable=True),
        sa.Column("cwd_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["agent_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_instance_id", "remote_session_id", name="uq_session_pointers_agent_remote"),
    )
    op.create_index("ix_session_pointers_agent_instance_id", "session_pointers", ["agent_instance_id"], unique=False)

    op.create_table(
        "run_headers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("session_pointer_id", sa.Uuid(), nullable=True),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["agent_instances.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_pointer_id"], ["session_pointers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_run_headers_session_pointer_id", "run_headers", ["session_pointer_id"], unique=False)
    op.create_index("ix_run_headers_agent_instance_id", "run_headers", ["agent_instance_id"], unique=False)

    op.create_table(
        "approval_headers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_header_id", sa.Uuid(), nullable=False),
        sa.Column("remote_approval_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_header_id"], ["run_headers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_header_id", "remote_approval_id", name="uq_approval_headers_run_remote"),
    )
    op.create_index("ix_approval_headers_run_header_id", "approval_headers", ["run_header_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("machine_id", sa.Uuid(), nullable=True),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=True),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["agent_instances.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_approval_headers_run_header_id", table_name="approval_headers")
    op.drop_table("approval_headers")
    op.drop_index("ix_run_headers_agent_instance_id", table_name="run_headers")
    op.drop_index("ix_run_headers_session_pointer_id", table_name="run_headers")
    op.drop_table("run_headers")
    op.drop_index("ix_session_pointers_agent_instance_id", table_name="session_pointers")
    op.drop_table("session_pointers")
    op.drop_index("ix_machine_credentials_agent_instance_id", table_name="machine_credentials")
    op.drop_table("machine_credentials")
    op.drop_index("ix_machine_pairings_user_id", table_name="machine_pairings")
    op.drop_table("machine_pairings")
    op.drop_index("ix_agent_instances_daemon_id", table_name="agent_instances")
    op.drop_index("ix_agent_instances_machine_id", table_name="agent_instances")
    op.drop_table("agent_instances")
    op.drop_index("ix_machines_user_id", table_name="machines")
    op.drop_table("machines")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("auth_sessions")
    op.drop_table("users")
