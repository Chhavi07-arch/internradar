"""Owner-scoped maintenance jobs; no real providers or inbox polling are configured."""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import Engine, text

from internradar.models import utcnow
from internradar.ownership import owner_session
from internradar.private_mail import Keyring, disconnect, purge_expired
from internradar.scheduler import Job, Outcome, run_due


def _purge(engine: Engine, owner_id: UUID) -> Outcome:
    with owner_session(engine, owner_id) as session:
        purge_expired(session, owner_id, now=utcnow())
    return Outcome.COMPLETE


def _retry_revocations(
    engine: Engine, owner_id: UUID, keys: Keyring, revoke: Callable[[bytes], None]
) -> Outcome:
    # Disabled connections with retained ciphertext are the durable retry markers.
    # Never enumerate accounts or pull credentials outside the owner's RLS scope.
    with owner_session(engine, owner_id) as session:
        pending = (
            session.execute(
                text("""
                SELECT id FROM mail_connection WHERE owner_user_id = :owner
                    AND disabled_at IS NOT NULL AND encrypted_credentials IS NOT NULL
                ORDER BY disabled_at, id
            """),
                {"owner": owner_id},
            )
            .scalars()
            .all()
        )
    outcome = Outcome.COMPLETE
    for connection_id in pending:
        try:
            disconnect(engine, owner_id, connection_id, keys, revoke, now=utcnow())
        except Exception:
            # Continue independently; never publish provider errors or token material.
            outcome = Outcome.RETRY
    return outcome


def run_private_jobs(
    engine: Engine,
    owner_id: UUID,
    *,
    keys: Keyring | None = None,
    revoke: Callable[[bytes], None] | None = None,
) -> dict[Job, Outcome]:
    """Run due maintenance for a trusted owner ID; revocation is opt-in via injection.

    The callback must treat already-revoked tokens as success: a crash after revocation
    but before clearing ciphertext can cause the same call to be repeated.
    """
    if (keys is None) != (revoke is None):
        raise ValueError("keys and revoke must be configured together")
    outcomes = {}
    outcome = run_due(
        engine, Job.PRIVATE_PURGE, lambda: _purge(engine, owner_id), owner_id=owner_id
    )
    if outcome is not None:
        outcomes[Job.PRIVATE_PURGE] = outcome
    if keys is not None and revoke is not None:
        outcome = run_due(
            engine,
            Job.PRIVATE_REVOCATION,
            lambda: _retry_revocations(engine, owner_id, keys, revoke),
            owner_id=owner_id,
        )
        if outcome is not None:
            outcomes[Job.PRIVATE_REVOCATION] = outcome
    return outcomes
