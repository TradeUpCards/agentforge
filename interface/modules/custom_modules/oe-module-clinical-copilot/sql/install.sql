-- AgentForge Clinical Co-Pilot: module install schema
--
-- Idempotent: safe to run multiple times.
-- All INSERT statements use INSERT IGNORE.
-- All CREATE TABLE statements use IF NOT EXISTS.

-- -------------------------------------------------------
-- 1.  Auto-extract document categories
--     Inserted under parent = 1 ("Categories" root).
--     lft/rght are set to 0 so the nested-set tree rebuilder
--     (C_Document / CategoryTree) can renumber on next load.
--     The id values (9000, 9001) are well above existing core
--     ids (max ~67 in database.sql) so no collision risk.
-- -------------------------------------------------------

INSERT IGNORE INTO `categories`
    (`id`, `name`, `value`, `parent`, `lft`, `rght`, `aco_spec`, `codes`)
VALUES
    (9000, 'Lab Result (auto-extract)',  '', 1, 0, 0, 'patients|docs', ''),
    (9001, 'Intake Form (auto-extract)', '', 1, 0, 0, 'patients|docs', '');

-- -------------------------------------------------------
-- 2.  Extraction audit/result table
--     Graders inspect this to confirm extraction ran.
--     extraction_json holds the validated LabReport /
--     IntakeForm payload — NOT raw document text.
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS `co_pilot_extractions` (
    `id`                         INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    `patient_id`                 INT             NOT NULL
        COMMENT 'OpenEMR pid (maps to sentinel 999101-999104 in demo)',
    `doc_ref_id`                 VARCHAR(64)     NOT NULL
        COMMENT 'documents.id as string; FHIR DocumentReference on Thursday',
    `doc_type`                   VARCHAR(32)     NOT NULL
        COMMENT '"lab_pdf" | "intake_form"',
    `status`                     VARCHAR(16)     NOT NULL
        COMMENT '"ok" | "refused" | "error"',
    `extraction_json`            LONGTEXT        DEFAULT NULL
        COMMENT 'Validated LabReport/IntakeForm JSON — NOT raw document text',
    `n_blocks`                   INT             DEFAULT NULL,
    `extraction_confidence_avg`  DECIMAL(5, 4)   DEFAULT NULL,
    `agent_request_id`           VARCHAR(64)     DEFAULT NULL,
    `created_at`                 DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_patient`  (`patient_id`),
    INDEX `idx_doc_ref`  (`doc_ref_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4;

-- -------------------------------------------------------
-- 3.  FHIR round-trip traceback table
--     One row per derived OpenEMR record created from an
--     extraction.  Powers two requirements from W2 PRD §43:
--       (a) idempotency: re-processing a document does not
--           duplicate clinical-table rows
--       (b) traceability: every derived row in OpenEMR's
--           clinical tables (procedure_result, lists,
--           prescriptions) can be traced back to the source
--           co_pilot_extractions row, and from there to the
--           source documents.id
--     The UNIQUE constraint enforces the no-duplicate guarantee:
--     (extraction_id, target_table, source_block_id) tuples are
--     unique, so a retry of the same (doc_ref_id, doc_type)
--     after re-extraction skips rows that already round-tripped.
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS `co_pilot_fhir_links` (
    `id`                       INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    `co_pilot_extraction_id`   INT UNSIGNED    NOT NULL
        COMMENT 'FK to co_pilot_extractions.id',
    `target_table`             VARCHAR(48)     NOT NULL
        COMMENT 'OpenEMR table the row was inserted into (procedure_order, procedure_result, lists, prescriptions)',
    `target_record_id`         BIGINT UNSIGNED NOT NULL
        COMMENT 'Primary key of the inserted row in target_table',
    `source_block_id`          VARCHAR(64)     DEFAULT NULL
        COMMENT 'Docling block_id from the extraction this row was derived from (null for parent rows like procedure_order)',
    `resource_kind`            VARCHAR(32)     NOT NULL
        COMMENT '"observation" | "allergy" | "medication" | "procedure_order_parent"',
    `created_at`               DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uniq_extraction_target_block`
        (`co_pilot_extraction_id`, `target_table`, `source_block_id`),
    INDEX `idx_extraction` (`co_pilot_extraction_id`),
    INDEX `idx_target`     (`target_table`, `target_record_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4;

-- -------------------------------------------------------
-- P2 recovery: attempt-chain columns on co_pilot_extractions
--
-- All eleven additions use #IfMissingColumn so the block is a no-op
-- when this is a clean install that already has the column (the CREATE
-- TABLE above will have included them on first run).  On databases that
-- only ran the P1 schema these ALTERs add the missing columns safely.
-- -------------------------------------------------------

#IfMissingColumn co_pilot_extractions is_active
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `is_active` TINYINT(1) NOT NULL DEFAULT 1
    COMMENT '1 = latest attempt for this (doc_ref_id, doc_type); 0 = superseded';
#EndIf

#IfMissingColumn co_pilot_extractions template_id
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `template_id` VARCHAR(64) NULL
    COMMENT 'Prompt-template identifier used for this attempt';
#EndIf

#IfMissingColumn co_pilot_extractions model
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `model` VARCHAR(64) NULL
    COMMENT 'LLM model identifier (e.g. claude-haiku-4-5)';
#EndIf

#IfMissingColumn co_pilot_extractions prompt_variant
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `prompt_variant` VARCHAR(32) NOT NULL DEFAULT 'default'
    COMMENT 'Named prompt variant (default | strict | lenient)';
#EndIf

#IfMissingColumn co_pilot_extractions attempt_n
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `attempt_n` INT NOT NULL DEFAULT 1
    COMMENT 'Ordinal attempt number within the (doc_ref_id, doc_type) sequence';
#EndIf

#IfMissingColumn co_pilot_extractions triggered_by
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `triggered_by` VARCHAR(32) NOT NULL DEFAULT 'initial'
    COMMENT '"initial" | "auto_retry" | "manual_retry"';
#EndIf

#IfMissingColumn co_pilot_extractions parent_extraction_id
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `parent_extraction_id` INT UNSIGNED NULL
    COMMENT 'FK to co_pilot_extractions.id for the attempt this was triggered from; NULL for initial';
#EndIf

#IfMissingColumn co_pilot_extractions cost_usd
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `cost_usd` DECIMAL(10, 6) NULL
    COMMENT 'Approximate LLM cost in USD for this attempt';
#EndIf

#IfMissingColumn co_pilot_extractions latency_ms
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `latency_ms` INT NULL
    COMMENT 'Agent-side end-to-end latency in milliseconds';
#EndIf

#IfMissingColumn co_pilot_extractions total_fields
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `total_fields` INT NULL
    COMMENT 'Total number of fields extracted';
#EndIf

#IfMissingColumn co_pilot_extractions stripped_fields
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `stripped_fields` INT NULL
    COMMENT 'Number of fields stripped by the verifier (no bbox citation)';
#EndIf

-- P3: Docling block inventory (for "Possibly Missed" pane in HITL UI)
#IfMissingColumn co_pilot_extractions docling_blocks_json
ALTER TABLE `co_pilot_extractions`
  ADD COLUMN `docling_blocks_json` LONGTEXT NULL
    COMMENT 'Full Docling block inventory from the agent response — JSON array of {block_id,page,bbox,text_snippet}; needed for "Possibly Missed" pane';
#EndIf

-- P2 recovery: indexes on co_pilot_extractions
#IfNotIndex co_pilot_extractions idx_active_attempt
ALTER TABLE `co_pilot_extractions`
  ADD INDEX `idx_active_attempt` (`doc_ref_id`, `doc_type`, `is_active`);
#EndIf

#IfNotIndex co_pilot_extractions idx_parent_extraction
ALTER TABLE `co_pilot_extractions`
  ADD INDEX `idx_parent_extraction` (`parent_extraction_id`);
#EndIf

-- -------------------------------------------------------
-- P3: Field-level verdict table
--
-- One row per field_verdicts entry from the agent response.
-- Links back to the extraction attempt and carries the bbox
-- coordinates needed for the HITL "Possibly Missed" overlay.
-- -------------------------------------------------------

#IfNotTable co_pilot_extracted_fields
CREATE TABLE `co_pilot_extracted_fields` (
    `id`               INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    `extraction_id`    INT UNSIGNED    NOT NULL
        COMMENT 'FK to co_pilot_extractions.id (ON DELETE RESTRICT)',
    `field_path`       VARCHAR(255)    NOT NULL
        COMMENT 'Dot-path from the agent schema, e.g. "results[0].test_name"',
    `status`           VARCHAR(16)     NOT NULL
        COMMENT '"verified" | "stripped" | "manually_edited" | "manually_added"',
    `source_block_id`  VARCHAR(64)     NULL
        COMMENT 'Docling block_id that backs this field; NULL when verifier could not find one',
    `bbox_json`        TEXT            NULL
        COMMENT 'JSON object {x,y,w,h} resolved by joining source_block_id against docling_blocks_json; NULL when source_block_id is NULL',
    `verifier_reason`  VARCHAR(64)     NULL
        COMMENT 'Reason code from the verifier when status=stripped (e.g. "block_not_found")',
    `clinical_table`   VARCHAR(48)     NULL
        COMMENT 'OpenEMR table the derived row was written to (procedure_result, lists, prescriptions); populated only for verified fields after roundtrip',
    `clinical_row_id`  INT             NULL
        COMMENT 'PK of the clinical-table row (signed INT to match OpenEMR core PKs); populated only for verified fields after roundtrip',
    `clinician_value`  TEXT            NULL
        COMMENT 'P4 R2: clinician-asserted override value. PHI-custody is CLINICIAN (not LLM). Never sent to the agent. Written by pencil-edit or assert-from-block UI. NULL when no override has been made.',
    `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_ef_extraction` (`extraction_id`),
    INDEX `idx_ef_field_path` (`extraction_id`, `field_path`(191)),
    CONSTRAINT `fk_ef_extraction`
        FOREIGN KEY (`extraction_id`) REFERENCES `co_pilot_extractions` (`id`)
        ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COMMENT = 'Per-field verdicts from the agent verifier, with bbox provenance for the HITL overlay';
#EndIf

-- -------------------------------------------------------
-- P4 R2: clinician_value column on co_pilot_extracted_fields
--
-- Idempotent via #IfMissingColumn (mirrors P3 pattern for
-- co_pilot_extractions attempt-chain columns).
-- PHI custody: clinician-asserted override; NEVER sent to the
-- agent; NEVER logged; PHP/DB-side only.
-- -------------------------------------------------------

#IfMissingColumn co_pilot_extracted_fields clinician_value
ALTER TABLE `co_pilot_extracted_fields`
  ADD COLUMN `clinician_value` TEXT NULL
    COMMENT 'P4 R2: clinician-asserted override value. PHI-custody is CLINICIAN (not LLM). Never sent to the agent. Written by pencil-edit or assert-from-block UI. NULL when no override has been made.';
#EndIf
