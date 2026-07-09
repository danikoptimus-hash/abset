"""Per-experiment access on top of the role model (abkit/auth/guards.py):
additional owners/editors (experiment_access table) and visibility restricted
by role (experiments.visible_roles) — Edit Properties modal, UX package.

See CLAUDE.md, section "Permissions model", for the full matrix this module
implements. Deliberately NOT used to gate run_analyze/run_validate_aa/
run_validate_ab in abkit/jobs.py (those stay open to any editor+ role, see the
comment on test_run_analyze_editor_allowed_on_others_experiment in
tests/test_jobs_permission_matrix.py) — it IS used to gate whether an
experiment is even visible/selectable in the first place (backend/routers/
experiments.py), which is what actually determines whether an editor can
reach Analyze/Validate for a given experiment at all.
"""

from __future__ import annotations

import uuid as uuid_mod

from abkit.auth.guards import AuthError, CurrentUser, require_role

_NOT_FETCHED = object()


def is_owner_or_granted(current_user: CurrentUser, exp_row, access_experiment_ids: set | None = _NOT_FETCHED) -> bool:
    """True for the original owner (owner_id), admin, or anyone with an
    experiment_access row (access='owner' or 'editor') — the set of people who
    can edit this experiment's blocks/status/name/properties or delete it.

    access_experiment_ids: precomputed set of experiment_ids the user has an
    experiment_access row for (from ExperimentAccessRepo.experiment_ids_for_user),
    to avoid one DB round-trip per experiment when checking a whole list. Pass
    nothing (default) for single-experiment checks — it fetches on demand."""
    if current_user.role == "admin":
        return True
    if str(current_user.id) == str(exp_row.owner_id):
        return True
    if access_experiment_ids is _NOT_FETCHED:
        from abkit.db.repositories import ExperimentAccessRepo

        return ExperimentAccessRepo().user_has_access(exp_row.id, uuid_mod.UUID(current_user.id))
    return exp_row.id in access_experiment_ids


def can_view_experiment(current_user: CurrentUser, exp_row, access_experiment_ids: set | None = _NOT_FETCHED) -> bool:
    """Owners/granted editors/admin always see it. Otherwise: draft is
    invisible; published respects visible_roles when set (None = everyone)."""
    if is_owner_or_granted(current_user, exp_row, access_experiment_ids):
        return True
    if exp_row.publication_status == "draft":
        return False
    if exp_row.visible_roles is not None:
        return current_user.role in exp_row.visible_roles
    return True


def require_view_experiment(current_user: CurrentUser, exp_row) -> CurrentUser:
    require_role(current_user, "viewer")
    if not can_view_experiment(current_user, exp_row):
        raise AuthError(f"Experiment '{exp_row.name}' not found")
    return current_user


def require_experiment_edit_access(current_user: CurrentUser, exp_row) -> CurrentUser:
    """Edit blocks, rename, operational/publication status, properties,
    delete: owner_id, an experiment_access grant, or admin. See CLAUDE.md
    'Permissions model' — Analyze/Validate are intentionally NOT gated here."""
    require_role(current_user, "editor")
    if is_owner_or_granted(current_user, exp_row):
        return current_user
    raise AuthError("You can only edit your own experiments (or contact an Admin)")
