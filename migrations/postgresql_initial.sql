-- Generated once for Alembic revision 0001. Do not regenerate after release.
-- statement
CREATE FUNCTION melvan_workspace_id() RETURNS TEXT LANGUAGE SQL STABLE AS $$ SELECT COALESCE(NULLIF(current_setting('melvan.workspace_id', true), ''), 'workspace-owner') $$
-- statement
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    nickname TEXT NOT NULL,
    workspace_type TEXT NOT NULL CHECK (workspace_type IN ('owner', 'administrator')),
    owner_user_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS one_owner_workspace
ON workspaces(workspace_type) WHERE workspace_type = 'owner'
-- statement
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended')),
    password_salt TEXT,
    password_hash TEXT,
    password_state TEXT NOT NULL DEFAULT 'bootstrap_required' CHECK (
        password_state IN ('bootstrap_required', 'password_change_required', 'active')
    ),
    temporary_password_expires_at TEXT,
    recovery_salt TEXT,
    recovery_hash TEXT,
    recovery_expires_at TEXT,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    total_logins INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
-- statement
CREATE TABLE IF NOT EXISTS workspace_memberships (
    user_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'administrator', 'reporter', 'reviewer')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, workspace_id, role),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
)
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS one_owner_role
ON user_roles(role) WHERE role = 'owner'
-- statement
CREATE TABLE IF NOT EXISTS logic_models (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 1,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    source TEXT NOT NULL,
    fixture_json TEXT NOT NULL,
    source_filename TEXT,
    media_type TEXT,
    content_hash TEXT,
    source_path TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT,
    uploaded_at TEXT,
    committed_by TEXT,
    committed_at TEXT,
    change_summary_json TEXT NOT NULL DEFAULT '{}',
    impact_json TEXT NOT NULL DEFAULT '{}',
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS elements (
    id TEXT PRIMARY KEY,
    display_id TEXT NOT NULL,
    logic_model_id TEXT,
    label TEXT NOT NULL,
    element_type TEXT NOT NULL CHECK (element_type IN ('Output', 'Outcome')),
    level TEXT NOT NULL,
    group_name TEXT,
    source TEXT NOT NULL CHECK (source IN ('fixture', 'model', 'reviewer')),
    status TEXT NOT NULL CHECK (status IN ('published', 'provisional', 'approved')),
    created_at TEXT NOT NULL,
    stable_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    display_number INTEGER,
    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS logic_model_drafts (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    base_logic_model_id TEXT REFERENCES logic_models(id),
    draft_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
-- statement
CREATE TABLE IF NOT EXISTS element_relationships (
    from_element_id TEXT NOT NULL,
    to_element_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    PRIMARY KEY (from_element_id, to_element_id, relationship_type),
    FOREIGN KEY (from_element_id) REFERENCES elements(id),
    FOREIGN KEY (to_element_id) REFERENCES elements(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS statements (
    id TEXT PRIMARY KEY,
    original_text TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'awaiting_review', 'evidence', 'failed')),
    active_analysis_id TEXT,
    initial_classification TEXT,
    approved_classification TEXT,
    processing_error TEXT,
    current_context_revision_id TEXT,
    context_updated_at TEXT,
    program_irrelevant_at TEXT,
    program_irrelevant_by TEXT,
    program_irrelevant_analysis_id TEXT,
    program_irrelevant_reason TEXT,
    capture_method TEXT NOT NULL DEFAULT 'text'
        CHECK (capture_method IN ('text', 'voice', 'photo')),
    capture_candidate_text TEXT,
    capture_text_edited INTEGER NOT NULL DEFAULT 0
        CHECK (capture_text_edited IN (0, 1)),
    capture_provider TEXT,
    capture_provider_version TEXT,
    capture_processing_ms INTEGER,
    capture_result TEXT NOT NULL DEFAULT 'success',
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS statement_context_revisions (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    context_text TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (statement_id, version),
    FOREIGN KEY (statement_id) REFERENCES statements(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS media_staging (
    id TEXT PRIMARY KEY,
    reporter_id TEXT NOT NULL,
    session_fingerprint TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('camera', 'library')),
    storage_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    candidate_text TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    processing_ms INTEGER NOT NULL,
    processing_result TEXT NOT NULL CHECK (processing_result IN ('success', 'no_text')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS statement_attachments (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL UNIQUE,
    reporter_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('camera', 'library')),
    storage_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    processing_ms INTEGER NOT NULL,
    processing_result TEXT NOT NULL CHECK (processing_result IN ('success', 'no_text')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    summary TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('Activity', 'Output', 'Outcome', 'Indicator')),
    classification_confidence REAL NOT NULL,
    classification_rationale TEXT NOT NULL,
    result_direction TEXT CHECK (result_direction IN ('Positive', 'Negative')),
    outcome_category TEXT,
    outcome_change_types TEXT NOT NULL DEFAULT '[]',
    match_status TEXT NOT NULL CHECK (match_status IN ('existing_match', 'no_suitable_match')),
    mapping_confidence REAL NOT NULL,
    mapping_rationale TEXT NOT NULL,
    proposed_element_id TEXT,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    logic_model_version TEXT NOT NULL,
    logic_model_id TEXT,
    context_revision_id TEXT,
    reviewer_instruction TEXT,
    learning_policy_version TEXT,
    learning_memory_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (learning_memory_enabled IN (0, 1)),
    learning_snapshot_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (statement_id, version),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (proposed_element_id) REFERENCES elements(id) ON DELETE SET NULL,
    FOREIGN KEY (context_revision_id) REFERENCES statement_context_revisions(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS analysis_mappings (
    analysis_id TEXT NOT NULL,
    element_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    confidence REAL,
    rationale TEXT,
    PRIMARY KEY (analysis_id, element_id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE,
    FOREIGN KEY (element_id) REFERENCES elements(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS review_feedback (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    corrected_classification TEXT,
    corrected_result_direction TEXT CHECK (corrected_result_direction IN ('Positive', 'Negative')),
    corrected_outcome_category TEXT CHECK (corrected_outcome_category IN ('A', 'B', 'C')),
    corrected_outcome_change_types TEXT NOT NULL DEFAULT '[]',
    comments TEXT,
    language_issue_types TEXT NOT NULL DEFAULT '[]',
    mapping_ids TEXT NOT NULL DEFAULT '[]',
    created_element_id TEXT,
    rejected_proposal_snapshot TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL UNIQUE,
    analysis_id TEXT NOT NULL,
    accepted_by TEXT,
    accepted_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS logic_model_activations (
    id TEXT PRIMARY KEY,
    logic_model_id TEXT NOT NULL,
    previous_logic_model_id TEXT,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('upload', 'commit', 'restore')),
    activated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed' CHECK (
        status IN ('preparing', 'completed', 'failed', 'no_op')
    ),
    impact_manifest_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    completed_at TEXT,
    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id),
    FOREIGN KEY (previous_logic_model_id) REFERENCES logic_models(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS evidence_assessments (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    logic_model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'not_relevant')),
    suggested_mapping_ids TEXT NOT NULL DEFAULT '[]',
    suggested_mapping_details TEXT NOT NULL DEFAULT '[]',
    mapping_rationale TEXT NOT NULL DEFAULT '',
    suggested_classification TEXT,
    confirmed_classification TEXT CHECK (
        confirmed_classification IN ('Activity', 'Output', 'Outcome', 'Indicator')
    ),
    suggested_result_direction TEXT CHECK (
        suggested_result_direction IN ('Positive', 'Negative')
    ),
    confirmed_result_direction TEXT CHECK (
        confirmed_result_direction IN ('Positive', 'Negative')
    ),
    reviewer_id TEXT,
    reviewer_feedback TEXT,
    revision_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    accepted_analysis_id TEXT,
    source_logic_model_id TEXT,
    source_assessment_id TEXT,
    activation_id TEXT,
    change_set_json TEXT NOT NULL DEFAULT '[]',
    suggestion_version INTEGER NOT NULL DEFAULT 1,
    disposition_reason TEXT,
    UNIQUE (evidence_id, logic_model_id),
    FOREIGN KEY (evidence_id) REFERENCES evidence(id),
    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id),
    FOREIGN KEY (accepted_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (source_logic_model_id) REFERENCES logic_models(id),
    FOREIGN KEY (source_assessment_id) REFERENCES evidence_assessments(id),
    FOREIGN KEY (activation_id) REFERENCES logic_model_activations(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS evidence_assessment_mappings (
    assessment_id TEXT NOT NULL,
    element_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    confidence REAL,
    rationale TEXT,
    PRIMARY KEY (assessment_id, element_id),
    FOREIGN KEY (assessment_id) REFERENCES evidence_assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (element_id) REFERENCES elements(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS review_sessions (
    id TEXT PRIMARY KEY,
    reviewer_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    completion_reason TEXT CHECK (completion_reason IN ('exhausted', 'ended')),
    scoring_policy_version TEXT NOT NULL,
    report_json TEXT,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS one_open_review_session_per_reviewer
ON review_sessions(reviewer_id) WHERE status = 'open'
-- statement
CREATE TABLE IF NOT EXISTS review_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    statement_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    resulting_analysis_id TEXT,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'changed', 'not_relevant')),
    is_self_review INTEGER NOT NULL DEFAULT 0 CHECK (is_self_review IN (0, 1)),
    comments TEXT,
    created_at TEXT NOT NULL,
    evidence_assessment_id TEXT,
    FOREIGN KEY (session_id) REFERENCES review_sessions(id),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (resulting_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS scoring_events (
    id TEXT PRIMARY KEY,
    cycle_key TEXT NOT NULL UNIQUE,
    cycle_type TEXT NOT NULL CHECK (cycle_type IN ('statement', 'evidence_reassessment')),
    statement_id TEXT,
    evidence_assessment_id TEXT,
    first_review_event_id TEXT NOT NULL UNIQUE,
    first_review_approval INTEGER NOT NULL CHECK (first_review_approval IN (0, 1)),
    correct_level INTEGER NOT NULL CHECK (correct_level IN (0, 1)),
    correct_direction INTEGER CHECK (correct_direction IN (0, 1)),
    correct_mapping INTEGER NOT NULL CHECK (correct_mapping IN (0, 1)),
    overall_hygiene INTEGER NOT NULL CHECK (overall_hygiene IN (0, 1)),
    source_signals_json TEXT NOT NULL,
    explanations_json TEXT NOT NULL,
    scoring_policy_version TEXT NOT NULL,
    interpretation_model_version TEXT,
    interpretation_prompt_version TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id),
    FOREIGN KEY (first_review_event_id) REFERENCES review_events(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS learning_policies (
    version TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS learning_examples (
    id TEXT PRIMARY KEY,
    review_event_id TEXT NOT NULL,
    statement_id TEXT NOT NULL,
    source_analysis_id TEXT NOT NULL,
    resulting_analysis_id TEXT,
    evidence_assessment_id TEXT,
    example_key TEXT NOT NULL,
    dimension TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    original_value_json TEXT,
    final_value_json TEXT,
    original_confidence REAL,
    truth_weight REAL NOT NULL DEFAULT 1.0,
    surprise_score REAL NOT NULL,
    learning_priority REAL NOT NULL,
    source_text TEXT NOT NULL,
    logic_model_id TEXT,
    stable_element_id TEXT,
    reviewer_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    eligibility_status TEXT NOT NULL DEFAULT 'eligible',
    superseded_by TEXT,
    exclusion_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (review_event_id, example_key),
    FOREIGN KEY (review_event_id) REFERENCES review_events(id),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (source_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (resulting_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id),
    FOREIGN KEY (policy_version) REFERENCES learning_policies(version),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS language_feedback (
    id TEXT PRIMARY KEY,
    review_event_id TEXT NOT NULL UNIQUE,
    statement_id TEXT NOT NULL,
    issue_types_json TEXT NOT NULL DEFAULT '[]',
    comments TEXT,
    original_summary TEXT NOT NULL,
    final_summary TEXT,
    example_status TEXT NOT NULL CHECK (
        example_status IN ('positive', 'paired', 'negative_only')
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_event_id) REFERENCES review_events(id),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS learning_snapshots (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    analysis_id TEXT,
    policy_version TEXT NOT NULL,
    memory_enabled INTEGER NOT NULL CHECK (memory_enabled IN (0, 1)),
    eligible_example_ids_json TEXT NOT NULL DEFAULT '[]',
    retrieved_example_ids_json TEXT NOT NULL DEFAULT '[]',
    retrieval_error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (policy_version) REFERENCES learning_policies(version),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS analysis_learning_examples (
    analysis_id TEXT NOT NULL,
    learning_example_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    similarity REAL NOT NULL,
    ranking_score REAL NOT NULL,
    inclusion_reason TEXT NOT NULL,
    PRIMARY KEY (analysis_id, learning_example_id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE,
    FOREIGN KEY (learning_example_id) REFERENCES learning_examples(id),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS review_session_score_snapshots (
    session_id TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    scoring_policy_version TEXT NOT NULL,
    learning_policy_version TEXT NOT NULL,
    memory_enabled INTEGER NOT NULL CHECK (memory_enabled IN (0, 1)),
    scores_json TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES review_sessions(id),
    FOREIGN KEY (learning_policy_version) REFERENCES learning_policies(version),
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() REFERENCES workspaces(id)
)
-- statement
CREATE TABLE IF NOT EXISTS authenticated_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    active_role TEXT,
    workspace_id TEXT,
    session_kind TEXT NOT NULL DEFAULT 'full' CHECK (
        session_kind IN ('full', 'password_change')
    ),
    duration_choice TEXT NOT NULL DEFAULT 'midnight' CHECK (
        duration_choice IN ('midnight', '7_days', '28_days')
    ),
    timezone_name TEXT NOT NULL DEFAULT 'UTC',
    requested_expires_at TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
)
-- statement
CREATE INDEX IF NOT EXISTS authenticated_sessions_user
ON authenticated_sessions(user_id, expires_at)
-- statement
CREATE TABLE IF NOT EXISTS authentication_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    user_id TEXT,
    challenge_id TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
)
-- statement
CREATE TABLE IF NOT EXISTS authentication_locks (
    user_id TEXT PRIMARY KEY,
    locked_until TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cleared_at TEXT
)
-- statement
CREATE TABLE IF NOT EXISTS password_recovery_requests (
    id TEXT PRIMARY KEY,
    email_fingerprint TEXT NOT NULL,
    user_id TEXT,
    request_source_fingerprint TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
-- statement
CREATE INDEX IF NOT EXISTS password_recovery_email_created
ON password_recovery_requests(email_fingerprint, created_at DESC)
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS unique_user_email ON users(lower(email))
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS unique_workspace_nickname ON workspaces(lower(nickname))
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS one_current_logic_model_per_workspace ON logic_models(workspace_id, is_current) WHERE is_current = 1
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS one_active_learning_policy_per_workspace ON learning_policies(workspace_id, is_active) WHERE is_active = 1
-- statement
CREATE INDEX IF NOT EXISTS logic_models_workspace ON logic_models(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS elements_workspace ON elements(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS logic_model_drafts_workspace ON logic_model_drafts(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS element_relationships_workspace ON element_relationships(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS statements_workspace ON statements(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS statement_context_revisions_workspace ON statement_context_revisions(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS media_staging_workspace ON media_staging(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS statement_attachments_workspace ON statement_attachments(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS analyses_workspace ON analyses(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS analysis_mappings_workspace ON analysis_mappings(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS review_feedback_workspace ON review_feedback(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS evidence_workspace ON evidence(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS logic_model_activations_workspace ON logic_model_activations(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS evidence_assessments_workspace ON evidence_assessments(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS evidence_assessment_mappings_workspace ON evidence_assessment_mappings(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS review_sessions_workspace ON review_sessions(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS review_events_workspace ON review_events(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS scoring_events_workspace ON scoring_events(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS learning_policies_workspace ON learning_policies(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS learning_examples_workspace ON learning_examples(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS language_feedback_workspace ON language_feedback(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS learning_snapshots_workspace ON learning_snapshots(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS analysis_learning_examples_workspace ON analysis_learning_examples(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS review_session_score_snapshots_workspace ON review_session_score_snapshots(workspace_id)
-- statement
CREATE INDEX IF NOT EXISTS audit_events_workspace ON audit_events(workspace_id)
-- statement
INSERT INTO workspaces (id, name, nickname, workspace_type, owner_user_id, created_by, status, created_at, updated_at) VALUES ('workspace-owner', 'MELvan', 'MELvan', 'owner', 'user-admin', 'system-migration', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING
-- statement
INSERT INTO learning_policies (version, enabled, is_active, config_json, created_at, workspace_id) VALUES ('reviewer-memory-v1', 1, 1, '{"limit": 8, "rolling_window": 3, "minimum_events": 20}', CURRENT_TIMESTAMP, 'workspace-owner') ON CONFLICT DO NOTHING
