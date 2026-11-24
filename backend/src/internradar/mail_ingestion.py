"""Process normalized mail without coupling extraction to the delivery provider."""

import json
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import Engine

from internradar.gmail.classify import EXTRACTABLE, classify
from internradar.gmail.client import Email
from internradar.gmail.extract import Extractor
from internradar.models import utcnow
from internradar.ownership import owner_session
from internradar.private_mail import (
    Delivery,
    Keyring,
    complete_delivery,
    pending_payload,
    stage_delivery,
)


def accept_mail(
    engine: Engine, owner: UUID, connection: UUID, delivery_id: str, email: Email, keys: Keyring
) -> bool:
    """Adapters supply verified routing; the email itself cannot select the owner."""
    delivery = Delivery(owner, connection, delivery_id)
    data = asdict(email)
    data.pop("received_at")
    now = utcnow()
    with owner_session(engine, owner) as session:
        staged = stage_delivery(
            session, delivery, email.received_at, json.dumps(data).encode(), keys, now=now
        )
        kind = classify(email)
        if staged and kind not in EXTRACTABLE:
            complete_delivery(session, delivery, kind.value, [], now=now)
        return staged


def process_mail(engine: Engine, delivery: Delivery, extractor: Extractor, keys: Keyring) -> bool:
    with owner_session(engine, delivery.owner_id) as session:
        pending = pending_payload(session, delivery, keys, now=utcnow())
    if pending is None:
        return False
    payload, received_at = pending
    email = Email(**json.loads(payload), received_at=received_at)
    kind = classify(email)
    results = extractor.extract(email) if kind in EXTRACTABLE else []
    with owner_session(engine, delivery.owner_id) as session:
        return complete_delivery(session, delivery, kind.value, results, now=utcnow())
