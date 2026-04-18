"""Initial schema — all ChemFlow tables.

Revision ID: 0001
Revises: (none — first migration)
Create Date: 2026-04-18

Table creation order respects foreign-key dependencies:
  1. users
  2. projects            (FK → users)
  3. simulations         (FK → projects)
  4. flowsheets          (FK → simulations, UNIQUE simulation_id)
  5. simulation_results  (FK → simulations, UNIQUE simulation_id)
  6. simulation_projects (legacy quick-run container)
  7. simulation_runs     (FK → simulation_projects)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(16), nullable=False, server_default="free"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── projects ───────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    # ── simulations ────────────────────────────────────────────────────────────
    op.create_table(
        "simulations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulations_project_id", "simulations", ["project_id"])

    # ── flowsheets ─────────────────────────────────────────────────────────────
    op.create_table(
        "flowsheets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("edges", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["simulation_id"], ["simulations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("simulation_id", name="uq_flowsheets_simulation_id"),
    )

    # ── simulation_results ─────────────────────────────────────────────────────
    op.create_table(
        "simulation_results",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("simulation_id", sa.String(), nullable=False),
        sa.Column("streams", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("energy_balance", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"], ["simulations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "simulation_id", name="uq_simulation_results_simulation_id"
        ),
    )

    # ── legacy: simulation_projects ────────────────────────────────────────────
    op.create_table(
        "simulation_projects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── legacy: simulation_runs ────────────────────────────────────────────────
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(64), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["simulation_projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("simulation_runs")
    op.drop_table("simulation_projects")
    op.drop_table("simulation_results")
    op.drop_table("flowsheets")
    op.drop_index("ix_simulations_project_id", table_name="simulations")
    op.drop_table("simulations")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
