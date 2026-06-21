PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  reply_to TEXT,
  sender TEXT NOT NULL,
  recipient TEXT NOT NULL,
  intent TEXT NOT NULL CHECK (intent IN ('TASK', 'RESULT', 'BLOCKER', 'REVIEW', 'QUESTION', 'NOTICE')),
  status TEXT NOT NULL CHECK (status IN ('open', 'claimed', 'in_progress', 'blocked', 'review', 'done', 'cancelled', 'expired')),
  priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'critical')),
  owner TEXT,
  owner_instance TEXT,
  request TEXT NOT NULL,
  expected_output TEXT NOT NULL,
  depends_on_json TEXT NOT NULL DEFAULT '[]',
  artifacts_json TEXT NOT NULL DEFAULT '[]',
  ingress_context_json TEXT NOT NULL DEFAULT '{}',
  execution_constraints_json TEXT NOT NULL DEFAULT '{}',
  work_fingerprint TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('created', 'claimed', 'started', 'blocked', 'review_requested', 'review_completed', 'result_attached', 'completed', 'cancelled', 'expired', 'notice')),
  message TEXT NOT NULL,
  artifacts_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS heartbeats (
  heartbeat_key TEXT PRIMARY KEY,
  runtime TEXT NOT NULL,
  runtime_instance_id TEXT,
  heartbeat_scope TEXT NOT NULL DEFAULT 'runtime' CHECK (heartbeat_scope IN ('runtime', 'instance')),
  control_surface TEXT,
  control_surface_key TEXT,
  status TEXT NOT NULL CHECK (status IN ('idle', 'busy', 'blocked', 'offline')),
  current_task_id TEXT,
  health TEXT NOT NULL CHECK (health IN ('ok', 'degraded', 'error')),
  summary TEXT,
  last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locks (
  lock_name TEXT PRIMARY KEY,
  owner_runtime TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_recipient_status ON tasks(recipient, status);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_instance ON tasks(owner, owner_instance);
CREATE INDEX IF NOT EXISTS idx_tasks_run_id ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_work_fingerprint ON tasks(work_fingerprint);
CREATE INDEX IF NOT EXISTS idx_events_task_id_created_at ON events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_heartbeats_runtime_last_seen ON heartbeats(runtime, last_seen);
CREATE INDEX IF NOT EXISTS idx_heartbeats_surface_key ON heartbeats(control_surface_key);
