-- Phase 0E prototype only. Run solely against the disposable db-tests database.
-- UUIDs are application-generated; no extension and no user-data schema is assumed.

CREATE TABLE documents (
    id uuid CONSTRAINT pk_documents PRIMARY KEY,
    owner_id text NOT NULL,
    scope text NOT NULL CONSTRAINT ck_documents_scope CHECK (scope IN ('personal', 'family', 'shared')),
    document_type text NOT NULL CONSTRAINT ck_documents_type CHECK (document_type IN ('journal', 'project', 'note', 'agent', 'family', 'other')),
    storage_root_key text NOT NULL,
    relative_path text NOT NULL CONSTRAINT ck_documents_relative_path CHECK (relative_path <> '' AND relative_path !~ '(^/|(^|/)\.\.(/|$))'),
    storage_mode text NOT NULL CONSTRAINT ck_documents_storage_mode CHECK (storage_mode IN ('legacy_file', 'shadow_indexed', 'db_managed', 'migration_blocked')),
    state text NOT NULL CONSTRAINT ck_documents_state CHECK (state IN ('active', 'deleted', 'missing', 'conflict')),
    current_revision_id uuid NULL,
    materialized_file_hash char(64) NULL,
    materialized_size bigint NULL CONSTRAINT ck_documents_size CHECK (materialized_size IS NULL OR materialized_size >= 0),
    materialized_mtime_ns bigint NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz NULL
);

CREATE UNIQUE INDEX uq_documents_active_path
    ON documents (scope, storage_root_key, relative_path) WHERE state <> 'deleted';

CREATE TABLE document_revisions (
    id uuid CONSTRAINT pk_document_revisions PRIMARY KEY,
    document_id uuid NOT NULL CONSTRAINT fk_document_revisions_document REFERENCES documents(id) ON DELETE RESTRICT,
    parent_revision_id uuid NULL CONSTRAINT fk_document_revisions_parent REFERENCES document_revisions(id) ON DELETE RESTRICT,
    merge_parent_revision_id uuid NULL CONSTRAINT fk_document_revisions_merge_parent REFERENCES document_revisions(id) ON DELETE RESTRICT,
    origin text NOT NULL CONSTRAINT ck_document_revisions_origin CHECK (origin IN ('jtracker', 'external', 'migration', 'restore', 'agent', 'system')),
    reason text NOT NULL CONSTRAINT ck_document_revisions_reason CHECK (reason IN ('explicit_save', 'interval_checkpoint', 'pre_write_checkpoint', 'external_import', 'delete', 'rename', 'conflict_resolution', 'migration')),
    file_hash char(64) NOT NULL,
    semantic_hash char(64) NULL,
    created_at timestamptz NOT NULL,
    actor_user_id text NULL,
    operation_id uuid NULL,
    summary text NOT NULL DEFAULT ''
);

ALTER TABLE documents ADD CONSTRAINT fk_documents_current_revision
    FOREIGN KEY (current_revision_id) REFERENCES document_revisions(id) ON DELETE RESTRICT;

CREATE TABLE blocks (
    id uuid CONSTRAINT pk_blocks PRIMARY KEY,
    document_id uuid NOT NULL CONSTRAINT fk_blocks_document REFERENCES documents(id) ON DELETE RESTRICT,
    anchor_at timestamptz NULL,
    block_type text NOT NULL,
    created_revision_id uuid NOT NULL CONSTRAINT fk_blocks_created_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    deleted_revision_id uuid NULL CONSTRAINT fk_blocks_deleted_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL,
    deleted_at timestamptz NULL
);

CREATE TABLE block_revisions (
    id uuid CONSTRAINT pk_block_revisions PRIMARY KEY,
    block_id uuid NOT NULL CONSTRAINT fk_block_revisions_block REFERENCES blocks(id) ON DELETE RESTRICT,
    document_revision_id uuid NOT NULL CONSTRAINT fk_block_revisions_document_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    content_hash char(64) NOT NULL,
    markdown_content text NOT NULL,
    origin text NOT NULL CONSTRAINT ck_block_revisions_origin CHECK (origin IN ('jtracker', 'external', 'migration', 'restore', 'agent', 'system')),
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_block_revisions_content UNIQUE (block_id, content_hash)
);

CREATE TABLE document_revision_blocks (
    document_revision_id uuid NOT NULL CONSTRAINT fk_document_revision_blocks_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    block_id uuid NOT NULL CONSTRAINT fk_document_revision_blocks_block REFERENCES blocks(id) ON DELETE RESTRICT,
    block_revision_id uuid NOT NULL CONSTRAINT fk_document_revision_blocks_block_revision REFERENCES block_revisions(id) ON DELETE RESTRICT,
    position integer NOT NULL CONSTRAINT ck_document_revision_blocks_position CHECK (position >= 0),
    CONSTRAINT pk_document_revision_blocks PRIMARY KEY (document_revision_id, block_id),
    CONSTRAINT uq_document_revision_blocks_position UNIQUE (document_revision_id, position)
);

CREATE TABLE document_snapshots (
    document_revision_id uuid CONSTRAINT pk_document_snapshots PRIMARY KEY REFERENCES document_revisions(id) ON DELETE RESTRICT,
    markdown_content text NOT NULL,
    file_hash char(64) NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE document_working_states (
    document_id uuid CONSTRAINT pk_document_working_states PRIMARY KEY REFERENCES documents(id) ON DELETE RESTRICT,
    base_revision_id uuid NULL CONSTRAINT fk_document_working_states_base_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    observed_file_hash char(64) NOT NULL,
    observed_content text NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    checkpoint_due_at timestamptz NOT NULL
);

CREATE TABLE sync_observations (
    id uuid CONSTRAINT pk_sync_observations PRIMARY KEY,
    document_id uuid NOT NULL CONSTRAINT fk_sync_observations_document REFERENCES documents(id) ON DELETE RESTRICT,
    observed_file_hash char(64) NULL,
    observed_at timestamptz NOT NULL,
    status text NOT NULL,
    detail text NOT NULL DEFAULT ''
);

CREATE TABLE sync_conflicts (
    id uuid CONSTRAINT pk_sync_conflicts PRIMARY KEY,
    document_id uuid NOT NULL CONSTRAINT fk_sync_conflicts_document REFERENCES documents(id) ON DELETE RESTRICT,
    status text NOT NULL CONSTRAINT ck_sync_conflicts_status CHECK (status IN ('open', 'resolved')),
    base_revision_id uuid NULL CONSTRAINT fk_sync_conflicts_base_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL,
    resolved_at timestamptz NULL,
    resolution text NULL
);

CREATE TABLE sync_conflict_versions (
    id uuid CONSTRAINT pk_sync_conflict_versions PRIMARY KEY,
    conflict_id uuid NOT NULL CONSTRAINT fk_sync_conflict_versions_conflict REFERENCES sync_conflicts(id) ON DELETE RESTRICT,
    kind text NOT NULL CONSTRAINT ck_sync_conflict_versions_kind CHECK (kind IN ('base', 'external', 'jtracker', 'merged')),
    file_hash char(64) NOT NULL,
    markdown_content text NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_sync_conflict_versions_kind UNIQUE (conflict_id, kind)
);

CREATE TABLE pending_file_operations (
    id uuid CONSTRAINT pk_pending_file_operations PRIMARY KEY,
    document_id uuid NOT NULL CONSTRAINT fk_pending_file_operations_document REFERENCES documents(id) ON DELETE RESTRICT,
    idempotency_key text NOT NULL,
    status text NOT NULL CONSTRAINT ck_pending_file_operations_status CHECK (status IN ('prepared', 'file_written', 'finalized', 'conflict', 'failed')),
    base_file_hash char(64) NOT NULL,
    target_file_hash char(64) NOT NULL,
    target_markdown_content text NOT NULL,
    target_revision_id uuid NOT NULL CONSTRAINT fk_pending_file_operations_target_revision REFERENCES document_revisions(id) ON DELETE RESTRICT,
    error_code text NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    finalized_at timestamptz NULL,
    CONSTRAINT uq_pending_file_operations_idempotency UNIQUE (document_id, idempotency_key)
);

ALTER TABLE document_revisions ADD CONSTRAINT fk_document_revisions_operation
    FOREIGN KEY (operation_id) REFERENCES pending_file_operations(id) ON DELETE RESTRICT;

CREATE INDEX ix_document_revisions_document_created_at ON document_revisions (document_id, created_at DESC);
CREATE INDEX ix_blocks_document_anchor_at ON blocks (document_id, anchor_at);
CREATE INDEX ix_sync_observations_document_observed_at ON sync_observations (document_id, observed_at DESC);
CREATE INDEX ix_pending_file_operations_open ON pending_file_operations (updated_at) WHERE status IN ('prepared', 'file_written');
