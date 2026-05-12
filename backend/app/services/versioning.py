"""Generic helpers for the 'one active version among many' lifecycle.

Several kinds of versioned rows in this app share an identical pattern:
- `SceneAsset` (image / video / lipsync variants per scene)
- `ScenePromptVersion` (image-prompt / video-prompt versions per scene)
- `CharacterAsset` (portrait variants per character)

Each has:
- A SCOPE (e.g. `scene_id + asset_type`, `character_id`, …)
- An `is_active` boolean flag (only one True per scope at a time)
- A `created_at` timestamp (used to pick the next active after a delete)

Without this module the same "deactivate-priors-then-set-active" and
"promote-most-recent-after-delete" logic gets reinvented in every router
and service. Centralizing it here:
- guarantees consistent invariants ("exactly one active per scope")
- lets a new versioned model plug in with one helper call instead of ~30
  lines of imperative SQL
"""

from __future__ import annotations
from typing import Any, Callable, Optional, Type

from sqlmodel import Session, select


def make_active(
    db: Session,
    *,
    target: Any,
    siblings_filter: list,
    on_active_change: Optional[Callable[[Any], None]] = None,
) -> None:
    """Make `target` the sole active row in its scope.

    Args:
        target: a SQLModel instance with an `is_active` field. Can be newly
            constructed (not yet flushed) or an existing row.
        siblings_filter: a list of `Model.field == value` predicates that
            match every row in the same scope as `target`. The function
            queries `select(Model).where(*siblings_filter, Model.is_active == True)`.
        on_active_change: optional callback called with `target` after it
            becomes the active row — use this to update any "compat
            pointer" on a parent row (e.g. `Scene.reference_image_path`).

    The function `db.add`s the affected rows but does NOT commit; the
    caller controls the transaction boundary.
    """
    model: Type = type(target)
    priors = db.exec(
        select(model).where(*siblings_filter, model.is_active == True)  # noqa: E712
    ).all()
    target_id = getattr(target, "id", None)
    for p in priors:
        if target_id is not None and getattr(p, "id", None) == target_id:
            continue
        p.is_active = False
        db.add(p)
    target.is_active = True
    db.add(target)
    if on_active_change is not None:
        on_active_change(target)


def delete_and_promote(
    db: Session,
    *,
    deleted: Any,
    siblings_filter: list,
    on_active_change: Optional[Callable[[Optional[Any]], None]] = None,
) -> Optional[Any]:
    """Delete `deleted` and, if it was the active row, promote the most-
    recently-created remaining sibling to active.

    Args:
        deleted: the SQLModel instance to delete.
        siblings_filter: same shape as `make_active` — predicates that match
            every row in the same scope. (Must NOT include the `is_active`
            condition; we want all siblings regardless of active state.)
        on_active_change: optional callback. Called with the newly-active
            row, OR with `None` if no siblings remain. Use this to update
            a parent's compat pointer (e.g. clear `Scene.video_path` when
            the last video variant is deleted).

    Returns the new-active row, or None if no siblings remain.
    """
    was_active = bool(getattr(deleted, "is_active", False))
    model: Type = type(deleted)
    deleted_id = getattr(deleted, "id", None)
    db.delete(deleted)
    db.flush()  # ensure the delete is visible to the next query
    if not was_active:
        return None
    nxt = db.exec(
        select(model)
        .where(*siblings_filter)
        .where(model.id != deleted_id)  # extra safety
        .order_by(model.created_at.desc())
    ).first()
    if nxt is not None:
        nxt.is_active = True
        db.add(nxt)
    if on_active_change is not None:
        on_active_change(nxt)
    return nxt
