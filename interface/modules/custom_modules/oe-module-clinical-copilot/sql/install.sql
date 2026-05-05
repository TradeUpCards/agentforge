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
