"""Versioned SQLite persistence for InferenceOps records."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid


SCHEMA_VERSION = 2


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.migrate()

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def migrate(self):
        with self.connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
                conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            conn.execute("""CREATE TABLE IF NOT EXISTS trace_batches (
                id TEXT PRIMARY KEY, created REAL NOT NULL, source TEXT NOT NULL,
                dataset_hash TEXT NOT NULL, record_count INTEGER NOT NULL, metadata TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT NOT NULL, batch_id TEXT NOT NULL, endpoint TEXT NOT NULL,
                model TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(batch_id, trace_id), FOREIGN KEY(batch_id) REFERENCES trace_batches(id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, created REAL NOT NULL,
                type TEXT NOT NULL, endpoint TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, experiment_id TEXT,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,
                created REAL NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)""")
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            if version < 2:
                columns = conn.execute("PRAGMA table_info(traces)").fetchall()
                primary = [row[1] for row in columns if row[5]]
                if primary == ["trace_id"]:
                    conn.execute("ALTER TABLE traces RENAME TO traces_v1")
                    conn.execute("""CREATE TABLE traces (
                        trace_id TEXT NOT NULL, batch_id TEXT NOT NULL, endpoint TEXT NOT NULL,
                        model TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL,
                        PRIMARY KEY(batch_id, trace_id), FOREIGN KEY(batch_id) REFERENCES trace_batches(id))""")
                    conn.execute("INSERT INTO traces SELECT * FROM traces_v1")
                    conn.execute("DROP TABLE traces_v1")
                conn.execute("UPDATE schema_version SET version=2")

    def add_trace_batch(self, source, records, dataset_hash, metadata=None):
        batch_id = uuid.uuid4().hex[:12]
        created = time.time()
        metadata = metadata or {}
        with self.connection() as conn:
            conn.execute("INSERT INTO trace_batches VALUES (?, ?, ?, ?, ?, ?)",
                         (batch_id, created, source, dataset_hash, len(records), json.dumps(metadata)))
            conn.executemany("INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?)", [
                (row.trace_id, batch_id, row.endpoint, row.model, created, json.dumps(row.to_dict()))
                for row in records
            ])
        return batch_id

    def list_batches(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM trace_batches ORDER BY created DESC").fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def traces(self, batch_id=None):
        query, args = "SELECT payload FROM traces", ()
        if batch_id:
            query, args = query + " WHERE batch_id=?", (batch_id,)
        with self.connection() as conn:
            rows = conn.execute(query, args).fetchall()
        return [json.loads(row[0]) for row in rows]

    def replace_opportunities(self, batch_id, findings):
        created = time.time()
        with self.connection() as conn:
            conn.execute("DELETE FROM opportunities WHERE batch_id=?", (batch_id,))
            for finding in findings:
                persisted_id = f"{batch_id[:5]}-{finding['id']}"
                payload = {**finding, "id": persisted_id, "batch_id": batch_id, "created": created}
                conn.execute("INSERT INTO opportunities VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (persisted_id, batch_id, created, finding["type"], finding["endpoint"],
                              finding["status"], json.dumps(payload)))
        return self.opportunities(batch_id)

    def opportunities(self, batch_id=None):
        query, args = "SELECT payload FROM opportunities", ()
        if batch_id:
            query, args = query + " WHERE batch_id=?", (batch_id,)
        query += " ORDER BY created DESC"
        with self.connection() as conn:
            rows = conn.execute(query, args).fetchall()
        return [json.loads(row[0]) for row in rows]

    def opportunity(self, opportunity_id):
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM opportunities WHERE id=?", (opportunity_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def create_workflow(self, opportunity_id, state="DISCOVERED", payload=None):
        workflow_id, now = uuid.uuid4().hex[:12], time.time()
        record = {"id": workflow_id, "opportunity_id": opportunity_id, "experiment_id": None,
                  "state": state, "created": now, "updated": now, **(payload or {})}
        with self.connection() as conn:
            conn.execute("INSERT INTO workflows VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (workflow_id, opportunity_id, None, state, now, now, json.dumps(record)))
        self.add_event(workflow_id, "workflow_created", "success", {"state": state})
        return record

    def workflow(self, workflow_id):
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        result["events"] = self.events(workflow_id)
        return result

    def workflows(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT payload FROM workflows ORDER BY updated DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_workflow(self, workflow_id, state=None, **changes):
        current = self.workflow(workflow_id)
        if not current:
            raise KeyError("Workflow not found")
        current.pop("events", None)
        now = time.time()
        if state:
            current["state"] = state
        current.update(changes, updated=now)
        with self.connection() as conn:
            conn.execute("UPDATE workflows SET experiment_id=?, state=?, updated=?, payload=? WHERE id=?",
                         (current.get("experiment_id"), current["state"], now, json.dumps(current), workflow_id))
        return current

    def add_event(self, workflow_id, event_type, status, payload=None):
        with self.connection() as conn:
            conn.execute("INSERT INTO workflow_events(workflow_id,created,event_type,status,payload) VALUES (?,?,?,?,?)",
                         (workflow_id, time.time(), event_type, status, json.dumps(payload or {})))

    def events(self, workflow_id):
        with self.connection() as conn:
            rows = conn.execute("SELECT created,event_type,status,payload FROM workflow_events WHERE workflow_id=? ORDER BY id",
                                (workflow_id,)).fetchall()
        return [{"created": row["created"], "type": row["event_type"], "status": row["status"],
                 "payload": json.loads(row["payload"])} for row in rows]

    def summary(self):
        batches = self.list_batches()
        latest = batches[0]["id"] if batches else None
        opportunities = self.opportunities(latest) if latest else []
        traces = self.traces(latest) if latest else []
        return {
            "trace_count": len(traces), "batch_count": len(batches),
            "observed_cost": round(sum(row["cost"] for row in traces), 6),
            "currency": traces[0]["currency"] if traces else "USD",
            "opportunity_count": len(opportunities),
            "potential_savings": round(sum(row["estimated_savings"] for row in opportunities), 6),
            "workflow_count": len(self.workflows()), "latest_batch_id": latest,
        }
