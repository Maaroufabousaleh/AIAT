"""AIAT identity-service package.

The service is the sole owner of mailbox identity state on the mail edge.  It
contains no worker-facing secret-export API and is intentionally independent of
the laptop's MAS Postgres database.
"""

from .main import create_app

__all__ = ["create_app"]
