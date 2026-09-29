import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from lakecontext.compiler import compile_snapshot
from lakecontext.models import Snapshot, utcnow


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Metadata is sensitive too. New database files are owner-readable only.
        fd = (
            os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            if not self.path.exists()
            else None
        )
        if fd is not None:
            os.close(fd)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS graph (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions (
                    edge_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL
                );
            """)
            from lakecontext.semantics import init_schema

            init_schema(db)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _read(db):
        row = db.execute("SELECT body FROM graph WHERE id=1").fetchone()
        if not row:
            raise ValueError(
                "No metadata imported. Run 'lakecontext demo' or 'lakecontext ingest'."
            )
        graph = json.loads(row[0])
        decisions = {r[0]: r[1:] for r in db.execute("SELECT * FROM decisions")}
        for edge in graph["relationships"]:
            decision = decisions.get(edge["id"])
            if decision and decision[0] == edge["definition_fingerprint"]:
                edge.update(
                    status=decision[1],
                    review={
                        "reviewer": decision[2],
                        "reason": decision[3],
                        "reviewed_at": decision[4],
                    },
                )
        return graph

    def read(self):
        with self.connection() as db:
            return self._read(db)

    def ingest(self, snapshot: Snapshot):
        graph = compile_snapshot(snapshot)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT body FROM graph WHERE id=1").fetchone()
            if existing:
                old = json.loads(existing[0])
                if (old["source"], old["principal"]) != (graph["source"], graph["principal"]):
                    raise ValueError(
                        "Source/principal mismatch. Use a separate database per identity and source."
                    )
            db.execute("INSERT OR REPLACE INTO graph VALUES (1, ?)", (json.dumps(graph),))
            from lakecontext.semantics import invalidate_rules

            invalidate_rules(db, graph)
            valid = {e["id"]: e["definition_fingerprint"] for e in graph["relationships"]}
            for edge_id, fingerprint in db.execute(
                "SELECT edge_id, fingerprint FROM decisions"
            ).fetchall():
                if valid.get(edge_id) != fingerprint:
                    db.execute("DELETE FROM decisions WHERE edge_id=?", (edge_id,))
        return graph

    def review(self, edge_id: str, status: str, reviewer: str, reason: str):
        if status not in {"approved", "rejected"} or not reviewer.strip() or not reason.strip():
            raise ValueError("A valid decision, named reviewer, and reason are required")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            graph = self._read(db)
            edge = next((e for e in graph["relationships"] if e["id"] == edge_id), None)
            if edge is None:
                raise ValueError("Unknown relationship")
            db.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    edge_id,
                    edge["definition_fingerprint"],
                    status,
                    reviewer,
                    reason,
                    utcnow(),
                ),
            )
        return {"id": edge_id, "status": status, "reviewer": reviewer, "reason": reason}
