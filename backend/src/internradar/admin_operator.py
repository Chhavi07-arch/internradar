"""Grant or revoke an independently verified Google account in a local dev database."""

import argparse
import os
import sys
from getpass import getpass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from internradar.ownership import owner_session
from internradar.web_auth import ISSUER


def _change_role(engine: Engine, account_id: UUID, *, grant: bool) -> bool:
    desired_role = "admin" if grant else "customer"
    with owner_session(engine, account_id) as session:
        account = session.execute(
            text("""
                SELECT identity_issuer, identity_subject, role FROM public.account
                WHERE id = :id FOR UPDATE
            """),
            {"id": account_id},
        ).one_or_none()
        if account is None or account.identity_issuer != ISSUER or not account.identity_subject:
            raise ValueError("No matching Google account; complete Google web sign-in first")
        issuer, subject, previous_role = account
        if account_id != uuid5(NAMESPACE_URL, f"internradar:oidc:{issuer}:{subject}"):
            raise ValueError("The account reference does not match its Google identity")
        if previous_role == desired_role:
            return False
        if (
            grant
            and not session.execute(
                text("""
                SELECT EXISTS (SELECT 1 FROM public.web_session
                               WHERE owner_user_id = :id AND expires_at > now())
            """),
                {"id": account_id},
            ).scalar_one()
        ):
            raise ValueError("A current Google web sign-in is required for enrollment")
        changed = session.execute(
            text("""
                UPDATE public.account SET role = :role
                WHERE id = :id AND identity_issuer = :issuer AND identity_subject = :subject
                  AND role = :previous_role
                RETURNING id
            """),
            {
                "role": desired_role,
                "id": account_id,
                "issuer": issuer,
                "subject": subject,
                "previous_role": previous_role,
            },
        ).scalar_one()
        session.execute(
            text("""
                INSERT INTO internradar_admin.role_audit
                    (target_account_id, previous_role, new_role)
                VALUES (:account, :previous, :new)
            """),
            {"account": changed, "previous": previous_role, "new": desired_role},
        )
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("grant", "revoke"))
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.exit(2, "Enrollment requires an interactive local operator terminal.\n")

    engine = None
    try:
        url = make_url(os.environ.get("INTERNRADAR_ADMIN_OPERATOR_DATABASE_URL", ""))
        socket_host = url.query.get("host")
        socket_port = url.query.get("port", "5432")
        is_socket = (
            url.host is None
            and url.query.keys() <= {"host", "port"}
            and isinstance(socket_port, str)
            and socket_port.isascii()
            and socket_port.isdecimal()
            and len(socket_port) <= 5
            and 1 <= int(socket_port) <= 65535
            and isinstance(socket_host, str)
            and "," not in socket_host
            and Path(socket_host).is_absolute()
            and Path(socket_host).is_dir()
        )
        is_loopback_tcp = url.host in {"localhost", "127.0.0.1", "::1"} and not url.query
        if (
            url.drivername != "postgresql+psycopg"
            or not url.database
            or not (is_socket or is_loopback_tcp)
        ):
            parser.exit(2, "Configure an explicit local development operator database URL.\n")
        engine = create_engine(url, hide_parameters=True, poolclass=NullPool)
        with engine.connect() as conn:
            local_db = conn.scalar(
                text("""
                    SELECT current_database() = :name AND
                           CASE WHEN :socket THEN inet_server_addr() IS NULL
                                ELSE inet_server_addr() IN (inet '127.0.0.1', inet '::1') END
                """),
                {"name": url.database, "socket": is_socket},
            )
        if local_db is not True:
            parser.exit(2, "The connected database is not the specified local database.\n")
        prompt = f"Confirm {args.operation} in local dev database {url.database!r}; type its name: "
        if input(prompt) != url.database:
            parser.exit(2, "Database confirmation did not match.\n")
        try:
            account = UUID(getpass("Independently verified account reference (hidden): "))
        except ValueError:
            parser.exit(2, "Invalid account reference.\n")
        if (
            args.operation == "grant"
            and input(
                "After independently verifying the intended Google account signed in, "
                "type VERIFIED: "
            )
            != "VERIFIED"
        ):
            parser.exit(2, "Identity verification was not confirmed.\n")
        changed = _change_role(engine, account, grant=args.operation == "grant")
        print("Role change recorded." if changed else "Role already matched; no change made.")
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
    except SQLAlchemyError:
        parser.exit(2, "Operator database operation failed; no role change was committed.\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(2, "Operation cancelled.\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
