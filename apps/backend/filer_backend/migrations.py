from pathlib import Path

from alembic import command
from alembic.config import Config


def run_migrations() -> None:
    """Bring the local SQLite DB up to head. Invoked at FastAPI startup."""
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
