"""S8.5 backup/restore round trip (build bible §19 S8.5).

Live test (skipped when the environment can't run it — same posture as
the S8.3/S8.4 database tests). The scripts are executed end-to-end inside
the repo's own Postgres image (bash + pg_dump/pg_restore in-box), which
also works on machines without a usable bash (e.g. Windows):

1. scratch Postgres database + alembic + a probe table with known rows
   + an artifacts directory with one file
2. ``scripts/backup.sh`` → one ``qa-copilot-backup-*.tar.gz`` bundle
3. destroy the source of truth (TRUNCATE the probe table, delete the
   artifact)
4. ``scripts/restore.sh`` → the database comes back in a single
   transaction and the artifact reappears with identical content
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
TEST_DB = "qa_copilot_s85_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
PG_IMAGE = "pgvector/pgvector:pg16"  # the repo's own db image: bash + client tools


def _postgres_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5433), timeout=1.0):
            return True
    except OSError:
        return False


def _docker_image_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(["docker", "image", "inspect", PG_IMAGE], capture_output=True, text=True)
    return r.returncode == 0


needs_backup_stack = pytest.mark.skipif(
    not (_postgres_up() and _docker_image_ready()),
    reason="needs Postgres on 5433 + Docker with the pgvector image",
)


def _admin(sql: str) -> None:
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.fixture()
def scratch_db() -> Iterator[None]:
    """One scratch database (alembic to head), dropped afterwards."""
    try:
        _admin("SELECT 1")
    except Exception:  # noqa: BLE001 — any failure means "no database"
        pytest.skip("no Postgres reachable")

    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin(f"CREATE DATABASE {TEST_DB}")
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_URL  # alembic env.py: env var wins
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield
    finally:
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")


def _run_script(script: str, args: list[str], env: dict[str, str], data_dir: Path) -> None:
    """Run a repo script inside the pgvector image, against the host database.

    The scripts resolve REPO_ROOT from BASH_SOURCE (→ /repo), so the repo
    is mounted read-only at /repo; ``/data`` carries artifacts + backup
    output; ``host.docker.internal`` is Docker Desktop's loopback into the
    Windows host where Postgres listens on 5433.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{REPO_ROOT}:/repo:ro",
        "-v",
        f"{data_dir}:/data",
        "-w",
        "/repo",
        *(f"-e{k}={v}" for k, v in env.items()),
        PG_IMAGE,
        "bash",
        f"/repo/{script}",
        *args,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_backup_stack
def test_backup_restore_round_trip(tmp_path: Path, scratch_db: None) -> None:
    data = tmp_path / "data"
    src_artifacts = data / "src" / "artifacts"
    src_artifacts.mkdir(parents=True)

    # --- seed: probe table + one artifact -------------------------------------
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE s85_probe (id text PRIMARY KEY, payload text)"))
        conn.execute(
            text("INSERT INTO s85_probe VALUES ('row-1', 'hello s85'), ('row-2', 'world')")
        )
    engine.dispose()
    (src_artifacts / "run-42.txt").write_text("artifact-content-85", encoding="utf-8")

    # --- backup -----------------------------------------------------------------
    pg_url = f"postgresql://qa:qa@host.docker.internal:5433/{TEST_DB}"
    _run_script(
        "scripts/backup.sh",
        [],
        {
            "DATABASE_URL": pg_url,
            "PGPASSWORD": "qa",
            "BACKUP_DIR": "/data/backups",
            "ARTIFACTS_DIR": "/data/src/artifacts",
        },
        data,
    )
    bundles = sorted((data / "backups").glob("qa-copilot-backup-*.tar.gz"))
    assert len(bundles) == 1 and bundles[0].stat().st_size > 0

    # --- destroy the source of truth ---------------------------------------------
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE s85_probe"))
    engine.dispose()
    (src_artifacts / "run-42.txt").unlink()

    # --- restore into a fresh location ---------------------------------------------
    _run_script(
        "scripts/restore.sh",
        [f"/data/backups/{bundles[0].name}"],
        {"DATABASE_URL": pg_url, "PGPASSWORD": "qa", "ARTIFACTS_DIR": "/data/restored/artifacts"},
        data,
    )

    # --- verify: data and artifacts are back, byte-identical -------------------------
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT payload FROM s85_probe ORDER BY id")).fetchall()
    engine.dispose()
    assert [row[0] for row in rows] == ["hello s85", "world"]
    assert (data / "restored" / "artifacts" / "run-42.txt").read_text(encoding="utf-8") == (
        "artifact-content-85"
    )
