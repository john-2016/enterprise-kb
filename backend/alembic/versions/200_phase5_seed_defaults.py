"""seed built-in minimax provider + default models (Phase 5)

Revision ID: 200_phase5_seed_defaults
Revises: 100d1b19250f
Create Date: 2026-06-14 12:00:00.000000

Note:
- ``model_providers.is_builtin`` already exists since
  ``100d1b19250f_add_model_provider_config_ab_tables``. This revision is a
  logical / documentation marker for the v1→v2 boundary: from this point on,
  every install is expected to carry the built-in ``minimax`` provider plus
  the ``MiniMax-M3`` (chat) and ``embo-01`` (embedding) default models.
- The actual row inserts are NOT performed here — they live in
  ``scripts.seed.seed_default_models`` and are applied via
  ``scripts.migrate_v1_to_v2.migrate`` at app startup (see
  ``backend/main.py`` lifespan).
- A future migration that wants to back-fill defaults for legacy installs
  can import ``seed_default_models`` and call it inside ``upgrade()`` with
  a synchronous engine adapter; for now we keep this revision empty so the
  CI test suite can opt in / out by env var.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "200_phase5_seed_defaults"
down_revision: Union[str, None] = "100d1b19250f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No DDL changes — the is_builtin column was added in 100d1b19250f.
    # We assert it exists so any drift between model and DB surfaces loudly.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("model_providers")}
    if "is_builtin" not in cols:
        raise RuntimeError(
            "model_providers.is_builtin is missing — alembic and ORM are out "
            "of sync. Re-run 100d1b19250f or check downstream migrations."
        )


def downgrade() -> None:
    # Nothing to undo — schema is unchanged.
    pass