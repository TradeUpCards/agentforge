-- agent/seed_appointments.sql
--
-- Seed today's calendar with 5 appointments mapped to Synthea-imported
-- patients (pids 7-11 after a fresh dev-reset-install-demodata + 20 random
-- patients). Idempotent — re-running just adds another set; clear with:
--   DELETE FROM openemr_postcalendar_events WHERE pc_eventDate = CURDATE();
--
-- Run via:
--   docker compose exec mysql mariadb -uroot -proot openemr < seed_appointments.sql
--
-- Provider: pc_aid='6' (Donna Lee, physician — created by dev-easy install)
-- Facility: pc_facility=3 (Great Clinic — default in dev-easy)
-- Category: pc_catid=9 (Established Patient)

INSERT INTO openemr_postcalendar_events
(pc_catid, pc_multiple, pc_aid, pc_pid, pc_title, pc_time, pc_eventDate,
 pc_duration, pc_recurrtype, pc_recurrfreq, pc_startTime, pc_endTime,
 pc_alldayevent, pc_apptstatus, pc_facility, pc_topic, pc_eventstatus, pc_sharing)
VALUES
(9, UNIX_TIMESTAMP() + 1, '6', '7',  'Established patient — diabetes follow-up',     NOW(), CURDATE(), 1800, 0, 0, '09:00:00', '09:30:00', 0, '-', 3, 1, 1, 0),
(9, UNIX_TIMESTAMP() + 2, '6', '8',  'Established patient — hypertension follow-up', NOW(), CURDATE(), 1800, 0, 0, '09:30:00', '10:00:00', 0, '-', 3, 1, 1, 0),
(9, UNIX_TIMESTAMP() + 3, '6', '9',  'Established patient — annual physical',        NOW(), CURDATE(), 1800, 0, 0, '10:00:00', '10:30:00', 0, '-', 3, 1, 1, 0),
(9, UNIX_TIMESTAMP() + 4, '6', '10', 'Established patient — medication review',      NOW(), CURDATE(), 1800, 0, 0, '10:30:00', '11:00:00', 0, '-', 3, 1, 1, 0),
(9, UNIX_TIMESTAMP() + 5, '6', '11', 'Established patient — cardiac follow-up',      NOW(), CURDATE(), 1800, 0, 0, '11:00:00', '11:30:00', 0, '-', 3, 1, 1, 0);
