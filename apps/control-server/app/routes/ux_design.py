"""UX Design Lineage: Journey / Requirement / Artifact API (Epic #405).

Issue #407 implements the endpoints listed in
`docs/ux-design-lineage.md` §2.10 against the persistence and finite
vocabularies already declared in `app/db.py` / `app/models.py` (see the
"UX Design Lineage" banner comments in both). This module is a placeholder
that only registers the router shape so `app/main.py` and the rest of the
Control Server can boot while Issue #407's domain service
(`app/ux_design.py`) and its routes are implemented.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/ux-design", tags=["ux-design"])
