from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from abkit.config import DesignConfig


class DesignRequest(BaseModel):
    config: DesignConfig
    # Optional only for config.split_source == "external" (item 12) — that
    # flow needs no dataset at all; validated in backend/routers/design.py.
    dataset_id: str | None = None
    confirmed: bool = False
    # Item A3: which of the two buttons the "Overlap detected" dialog was
    # dismissed with. None — the dialog was never shown (no overlap, or
    # isolation != "warn"). "proceed" — the pre-existing "Continue despite the
    # overlap" (identical to the old confirmed=True and kept working exactly
    # as before). "exclude" — the new "Exclude overlapping & continue".
    #
    # Deliberately a SEPARATE field from `confirmed` rather than a third
    # confirmed-value: `confirmed` answers "may this design run at all",
    # overlap_action answers "and what should it do about the overlap" — an
    # older client that only knows `confirmed` keeps its exact old behavior
    # by leaving this None.
    overlap_action: Literal["proceed", "exclude"] | None = None
    # Item B2: optional planned end date, declared at design time. NOT part of
    # DesignConfig on purpose — it lives on the experiments row (like
    # started_at/completed_at, and like `dataset_id` here it's a design-time
    # input rather than a property of the design itself), so that editing it
    # later (Edit Properties, item B1/B2) and the auto-completion sweep (B3)
    # have exactly one source of truth instead of a column and a stale copy
    # inside the config JSONB.
    planned_end_date: date | None = None


class JobAccepted(BaseModel):
    job_id: str
