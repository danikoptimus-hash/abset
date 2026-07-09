"""Нормализация unit_id перед любым join/membership-сравнением с assignments.

ID — идентификатор, а не число: сравнение всегда должно быть строковым.
Без этого файловый режим (assignments.parquet сохраняет dtype как есть)
падает с "You are trying to merge on str and int64 columns for key
'unit_id'", если анализируемые данные загружены из CSV с числовым
unit_col (pandas по умолчанию парсит его как int64). Db-режим (Text-колонка,
abkit/db/repositories.py::AssignmentRepo.bulk_insert) этой проблеме не
подвержен, но эта же нормализация там безвредна (str(str) == str)."""

from __future__ import annotations

import pandas as pd


def normalize_id_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip()
