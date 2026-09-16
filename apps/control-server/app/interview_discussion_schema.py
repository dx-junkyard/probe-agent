"""Additive storage for reviewed interview contributions (IID-D)."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_discussion_extraction (
 id INTEGER PRIMARY KEY, system_id INTEGER NOT NULL REFERENCES systems(id),
 thread_id INTEGER NOT NULL REFERENCES assistant_discussion_thread(id),
 job_kind TEXT NOT NULL CHECK(job_kind IN ('extract','target_search')),
 input_digest TEXT NOT NULL, input_json TEXT NOT NULL, prompt_version TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
 result_json TEXT, error_code TEXT, attempt INTEGER NOT NULL DEFAULT 0,
 lease_until REAL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(thread_id, job_kind, input_digest, prompt_version)
);
CREATE TABLE IF NOT EXISTS interview_discussion_candidate (
 id INTEGER PRIMARY KEY, system_id INTEGER NOT NULL REFERENCES systems(id),
 thread_id INTEGER NOT NULL REFERENCES assistant_discussion_thread(id),
 extraction_id INTEGER REFERENCES interview_discussion_extraction(id),
 candidate_key TEXT NOT NULL, revision INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('supplement','correction','open_question','hypothesis')),
 text TEXT NOT NULL, author_kind TEXT NOT NULL CHECK(author_kind IN ('manual','reasoning_llm')),
 decision TEXT NOT NULL CHECK(decision IN ('pending','held','rejected','saved')),
 supersedes_candidate_id INTEGER REFERENCES interview_discussion_candidate(id),
 created_by TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(thread_id,candidate_key,revision)
);
CREATE TABLE IF NOT EXISTS interview_discussion_candidate_source (
 id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES interview_discussion_candidate(id),
 turn_id INTEGER REFERENCES assistant_discussion_turn(id), role TEXT NOT NULL,
 quote TEXT NOT NULL, quote_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_discussion_target (
 id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES interview_discussion_candidate(id),
 session_id INTEGER NOT NULL REFERENCES interview_session(id),
 target_kind TEXT NOT NULL CHECK(target_kind IN ('session','qa','intent_item','claim')),
 target_ref_json TEXT NOT NULL, captured_digest TEXT NOT NULL,
 reason TEXT NOT NULL, origin TEXT NOT NULL, selection TEXT NOT NULL
 CHECK(selection IN ('suggested','selected','rejected'))
);
CREATE TABLE IF NOT EXISTS interview_discussion_contribution (
 id INTEGER PRIMARY KEY, system_id INTEGER NOT NULL REFERENCES systems(id),
 thread_id INTEGER NOT NULL REFERENCES assistant_discussion_thread(id),
 candidate_id INTEGER NOT NULL UNIQUE REFERENCES interview_discussion_candidate(id),
 kind TEXT NOT NULL, text TEXT NOT NULL, source_manifest TEXT NOT NULL,
 supersedes_contribution_id INTEGER REFERENCES interview_discussion_contribution(id),
 reviewed_by TEXT NOT NULL, reviewed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_discussion_contribution_link (
 id INTEGER PRIMARY KEY, contribution_id INTEGER NOT NULL REFERENCES interview_discussion_contribution(id),
 session_id INTEGER NOT NULL REFERENCES interview_session(id),
 target_kind TEXT NOT NULL, target_ref_json TEXT NOT NULL, captured_digest TEXT NOT NULL,
 UNIQUE(contribution_id,session_id,target_kind,target_ref_json)
);
CREATE TABLE IF NOT EXISTS interview_discussion_operation (
 id INTEGER PRIMARY KEY, system_id INTEGER NOT NULL REFERENCES systems(id),
 actor_id TEXT NOT NULL, request_id TEXT NOT NULL, operation_kind TEXT NOT NULL,
 payload_digest TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(system_id,actor_id,request_id)
);
CREATE TABLE IF NOT EXISTS interview_discussion_application (
 id INTEGER PRIMARY KEY, contribution_link_id INTEGER NOT NULL UNIQUE REFERENCES interview_discussion_contribution_link(id),
 operation_id INTEGER NOT NULL REFERENCES interview_discussion_operation(id),
 domain_kind TEXT NOT NULL, domain_ref TEXT NOT NULL, result_revision_id INTEGER REFERENCES understanding_revision(id),
 applied_by TEXT NOT NULL, applied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_discussion_preview (
 id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, system_id INTEGER NOT NULL REFERENCES systems(id),
 actor_id TEXT NOT NULL, session_id INTEGER NOT NULL REFERENCES interview_session(id),
 manifest_json TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_discussion_outbox (
 id INTEGER PRIMARY KEY, operation_id INTEGER NOT NULL REFERENCES interview_discussion_operation(id),
 session_id INTEGER NOT NULL REFERENCES interview_session(id), system_id INTEGER NOT NULL REFERENCES systems(id),
 event_kind TEXT NOT NULL, delivery_state TEXT NOT NULL DEFAULT 'queued',
 attempt INTEGER NOT NULL DEFAULT 0, lease_until REAL, next_attempt_at REAL NOT NULL DEFAULT 0,
 UNIQUE(operation_id,session_id,event_kind)
);
CREATE TABLE IF NOT EXISTS interview_discussion_turn_request (
 id INTEGER PRIMARY KEY, system_id INTEGER NOT NULL REFERENCES systems(id),
 thread_id INTEGER NOT NULL REFERENCES assistant_discussion_thread(id), client_turn_id TEXT NOT NULL,
 payload_digest TEXT NOT NULL, user_turn_id INTEGER NOT NULL REFERENCES assistant_discussion_turn(id),
 reply_turn_id INTEGER UNIQUE REFERENCES assistant_discussion_turn(id), result_json TEXT,
 lease_until REAL NOT NULL, UNIQUE(thread_id,client_turn_id)
);
CREATE TABLE IF NOT EXISTS interview_discussion_revision_source (
 revision_id INTEGER NOT NULL REFERENCES understanding_revision(id),
 contribution_id INTEGER NOT NULL REFERENCES interview_discussion_contribution(id),
 PRIMARY KEY(revision_id,contribution_id)
);
CREATE INDEX IF NOT EXISTS iid_candidates_thread ON interview_discussion_candidate(thread_id,id);
CREATE INDEX IF NOT EXISTS iid_links_session ON interview_discussion_contribution_link(session_id,id);
"""
