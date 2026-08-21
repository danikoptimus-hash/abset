"""datasets.param_date_from/param_date_to/source_experiment_id

Плейсхолдеры дат в SQL-датасетах ({{date_from}}/{{date_to}}) и происхождение
датасета, собранного кнопкой «Fetch results dataset» на эксперименте.

param_date_from/param_date_to — значения, С КОТОРЫМИ БЫЛ СОБРАН текущий
снимок. Хранить их обязательно, а не выводить заново: без них
`POST /datasets/{id}/refresh` не знал бы, за какой период перевыполнять
запрос, и «обновить» означало бы «собрать за какой-то другой период»
— молча и не тем данным. Заодно это ответ на вопрос «а за какие даты вот
эта выборка», который иначе решается только чтением SQL.

Тип DATE, не timestamptz: параметр — календарный день (границы окна анализа),
а не момент; хранить его как инстант значило бы навязать произвольное
время суток и часовой пояс значению, которого пользователь не вводил. Тот же
выбор, что у experiments.planned_end_date (миграция 0023).

source_experiment_id — откуда взялся датасет результатов (ON DELETE SET NULL,
как datasets.experiment_id: удаление эксперимента не должно стирать собранные
по нему данные, только ссылку). Отличается от уже существующего
datasets.experiment_id по смыслу: тот — «первичная привязка/использование»,
этот — «этот датасет собран КНОПКОЙ на том эксперименте, его датами».

Всё аддитивно и nullable: у существующих датасетов параметров нет, и NULL
здесь навсегда означает «запрос без плейсхолдеров», а не «данные потеряны».

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("param_date_from", sa.Date(), nullable=True))
    op.add_column("datasets", sa.Column("param_date_to", sa.Date(), nullable=True))
    op.add_column(
        "datasets",
        sa.Column(
            "source_experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("datasets", "source_experiment_id")
    op.drop_column("datasets", "param_date_to")
    op.drop_column("datasets", "param_date_from")
