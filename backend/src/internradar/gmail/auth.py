"""Google OAuth for read-only Gmail access, one token file per connected inbox."""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from internradar.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class NotConnectedError(RuntimeError):
    pass


def client_secret_path() -> Path:
    return get_settings().data_dir / "google_client_secret.json"


def token_path(label: str) -> Path:
    return get_settings().data_dir / "tokens" / f"{label}.json"


def connect(label: str) -> Credentials:
    """Open the browser consent screen and store the resulting token."""
    secret = client_secret_path()
    if not secret.exists():
        raise NotConnectedError(
            f"missing {secret}. Download the OAuth client JSON from Google Cloud "
            "(see README: Gmail setup) and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent", open_browser=True)
    _save(label, credentials)
    return credentials


def load(label: str) -> Credentials:
    """Stored credentials for an inbox, refreshed if expired."""
    path = token_path(label)
    if not path.exists():
        raise NotConnectedError(
            f"inbox '{label}' is not connected: run `internradar gmail connect {label}`"
        )
    credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
    if credentials.valid:
        return credentials
    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        # Google expires refresh tokens after 7 days while the OAuth app is in "Testing".
        raise NotConnectedError(
            f"Google revoked the token for '{label}' (expected weekly while the app is in "
            f"Testing): run `internradar gmail connect {label}` again"
        ) from exc
    _save(label, credentials)
    return credentials


def is_connected(label: str) -> bool:
    return token_path(label).exists()


def _save(label: str, credentials: Credentials) -> None:
    path = token_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json())
    path.chmod(0o600)
