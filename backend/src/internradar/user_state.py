"""Owner-scoped state for shared catalogue cards."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session


def update_state(
    session: Session, owner_id: UUID, opportunity_id: int, **changes: Any
) -> dict[str, Any] | None:
    """Patch only supplied fields, without clobbering another independent update.

    Use an owner_session; the explicit owner predicate and RLS both apply.
    """
    mutable = ("seen_at", "bookmarked", "hidden", "notes")
    if not changes or set(changes).difference(mutable):
        raise ValueError("invalid catalogue state patch")
    fields = [name for name in mutable if name in changes]
    names = ", ".join(fields)
    values = ", ".join(f":{name}" for name in fields)
    updates = ", ".join(f"{name} = EXCLUDED.{name}" for name in fields)
    row = (
        session.execute(
            text(f"""
            INSERT INTO user_opportunity (owner_user_id, opportunity_id, {names})
            SELECT :owner, id, {values} FROM opportunity WHERE id = :opportunity
            ON CONFLICT (owner_user_id, opportunity_id) DO UPDATE SET {updates}
            RETURNING seen_at, bookmarked, hidden, notes
        """),
            {"owner": owner_id, "opportunity": opportunity_id, **changes},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None
