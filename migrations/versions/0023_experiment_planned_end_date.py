"""experiments.planned_end_date: optional planned end date + auto-completion

Item B2/B3 (test lifecycle dates): an experiment can declare, at design time,
when it is PLANNED to end; the value stays editable afterwards (Edit
Properties, item B1/B2) and drives the auto-completion sweep (B3: once
now >= planned_end_date and status == 'running', the experiment transitions to
'completed' on its own).

A real column rather than a key inside experiments.config (JSONB), for two
reasons that both matter here:
  - the sweep queries it (`WHERE status = 'running' AND planned_end_date <=
    now()`) on a schedule — a JSONB probe would be both slower and unindexable
    for what is a plain scalar;
  - it is EDITABLE after design. Keeping it in the design config would mean
    the config JSON (a snapshot of what was designed) and the current planned
    end date drift apart the first time somebody edits it. started_at/
    completed_at are already columns for exactly this reason; this is their
    sibling, not a design parameter.

DATE, not timestamptz: "the test is planned to end on the 20th" is a calendar
statement, and storing it as an instant would force an arbitrary
time-of-day/timezone choice into a value the user never expressed. The sweep
treats it as "end of that day, UTC" (see abkit/lifecycle.py::
planned_end_reached) rather than midnight, so a test planned to end on the
20th is not auto-completed at 00:00 on the 20th, when its last day has not
happened yet.

Additive and nullable — every existing experiment gets NULL ("no planned end
date"), which is also the permanent meaning of NULL, so there is no backfill
and nothing to interpret differently for old rows.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("planned_end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("experiments", "planned_end_date")
