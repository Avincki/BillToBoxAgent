"""Google Drive folder management (task 14).

Reuses the Google OAuth credentials minted for Gmail (task 8): the shared
``drive.file`` scope lets the app create and manage only the files it creates.
:class:`DriveConnector` wraps an injected Drive v3 service (tests pass a fake;
production builds it via :meth:`DriveConnector.from_config`) and exposes
**find-before-create** folder helpers so repeated runs converge on a single
folder tree instead of creating duplicates.

The quarter tree is ``<root>/<fy_label>/<quarter>/`` — e.g. ``Invoices/2026/Q1``.
The root ``Invoices`` folder is created on first run (decisions.md #9) unless a
``root_folder_id`` is pinned in config; either way it is resolved once and cached
on the instance. With ``drive.file`` scope ``files.list`` only ever sees the
app's own folders, so find-or-create-by-name re-finds the same root on a later
run without any pre-supplied id.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build

from billtobox_agent.config.models import DriveConfig, GoogleConfig
from billtobox_agent.mail.google_auth import load_credentials

FOLDER_MIME = "application/vnd.google-apps.folder"
# Drive's reserved alias for "My Drive" root — the parent of the Invoices folder.
DRIVE_ROOT_ALIAS = "root"


class DriveError(Exception):
    """Raised when a Drive folder operation cannot be completed."""


def _escape_query_value(value: str) -> str:
    """Escape a literal for a Drive v3 ``q`` string (backslash first, then quote)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveConnector:
    def __init__(
        self,
        service: Any,
        *,
        root_folder_id: str | None = None,
        root_folder_name: str = "Invoices",
    ) -> None:
        self._service = service
        self._root_folder_name = root_folder_name
        # When pinned in config we trust it; otherwise it is resolved (found or
        # created) on first use and memoised here for the rest of the process.
        self._root_folder_id = root_folder_id

    @classmethod
    def from_config(cls, google: GoogleConfig, drive: DriveConfig) -> DriveConnector:
        credentials = load_credentials(google)
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return cls(
            service,
            root_folder_id=drive.root_folder_id,
            root_folder_name=drive.root_folder_name,
        )

    @property
    def root_folder_name(self) -> str:
        return self._root_folder_name

    # ----- folder primitives --------------------------------------------------

    def find_folder(self, name: str, parent_id: str) -> str | None:
        """Return the id of the non-trashed child folder ``name`` under ``parent_id``."""
        query = (
            f"name = '{_escape_query_value(name)}' "
            f"and '{_escape_query_value(parent_id)}' in parents "
            f"and mimeType = '{FOLDER_MIME}' "
            "and trashed = false"
        )
        response = (
            self._service.files()
            .list(q=query, spaces="drive", fields="files(id, name)", pageSize=10)
            .execute()
        )
        files = response.get("files", [])
        if not files:
            return None
        folder_id = files[0].get("id")
        return folder_id if isinstance(folder_id, str) else None

    def create_folder(self, name: str, parent_id: str) -> str:
        metadata = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        folder = self._service.files().create(body=metadata, fields="id").execute()
        folder_id = folder.get("id")
        if not isinstance(folder_id, str) or not folder_id:
            raise DriveError(f"Drive returned no id when creating folder {name!r}")
        return folder_id

    def find_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return the id of ``name`` under ``parent_id``, creating it only if absent."""
        existing = self.find_folder(name, parent_id)
        if existing is not None:
            return existing
        return self.create_folder(name, parent_id)

    # ----- the Invoices/<fy_label>/<quarter> tree -----------------------------

    def ensure_root_folder(self) -> str:
        """Resolve (find-or-create) the root ``Invoices`` folder id, memoised."""
        root_id = self._root_folder_id
        if root_id is None:
            root_id = self.find_or_create_folder(self._root_folder_name, DRIVE_ROOT_ALIAS)
            self._root_folder_id = root_id
        return root_id

    def ensure_quarter_path(self, fy_label: str, quarter: str) -> str:
        """Find-or-create ``<root>/<fy_label>/<quarter>/`` and return the leaf id."""
        root_id = self.ensure_root_folder()
        year_id = self.find_or_create_folder(fy_label, root_id)
        return self.find_or_create_folder(quarter, year_id)
