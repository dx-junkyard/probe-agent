"""Authorization helpers for GitHub App installations (Issue #222).

An Installation Access Token is a credential for a GitHub organization, not
for a probe-agent System.  This module is the single DB-backed gate that
binds that credential to the Systems allowed to use it.  It deliberately
does not issue tokens or make network calls.
"""

from __future__ import annotations

import sqlite3


class GitHubInstallationAccessError(RuntimeError):
    """The installation is not an active assignment for the requested System."""


def require_active_installation_assignment(
    conn: sqlite3.Connection, installation_id: int, system_id: int
) -> sqlite3.Row:
    """Return an active installation assignment or fail closed.

    Every caller that is about to issue an Installation Access Token must use
    this helper first.  An absent registration, a disabled registration, and
    an assignment to a different System intentionally have the same error so
    the API does not disclose another tenant's GitHub installation state.
    """
    row = conn.execute(
        """
        SELECT i.*
        FROM github_installations AS i
        JOIN github_installation_systems AS s
          ON s.installation_id = i.installation_id
        WHERE i.installation_id = ?
          AND s.system_id = ?
          AND i.status = 'active'
        """,
        (installation_id, system_id),
    ).fetchone()
    if row is None:
        raise GitHubInstallationAccessError(
            "GitHub installation is not active and assigned to this System"
        )
    return row
