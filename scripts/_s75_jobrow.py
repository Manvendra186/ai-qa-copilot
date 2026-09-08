"""S7.5 debug: print the job row + ai_sessions for a given job id."""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://qa:qa@localhost:5433/qa_copilot"


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JOB_ID", "")
    engine = create_engine(DB)
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT id, type, status, progress, output_ref, error, "
                    "started_at, completed_at FROM jobs WHERE id = :j"
                ),
                {"j": job_id},
            )
            .mappings()
            .first()
        )
        print("job:", dict(row) if row else "NOT FOUND")
        rows = (
            conn.execute(
                text(
                    "SELECT id, kind, status, error FROM ai_sessions "
                    "WHERE job_id = :j ORDER BY created_at DESC LIMIT 5"
                ),
                {"j": job_id},
            )
            .mappings()
            .all()
        )
        for r in rows:
            print("ai_session:", dict(r))
        recent = (
            conn.execute(
                text(
                    "SELECT id, type, status, error, created_at "
                    "FROM jobs ORDER BY created_at DESC LIMIT 8"
                )
            )
            .mappings()
            .all()
        )
        print("recent jobs:")
        for r in recent:
            print("  ", dict(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
