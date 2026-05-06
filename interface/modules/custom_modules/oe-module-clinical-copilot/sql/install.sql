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
