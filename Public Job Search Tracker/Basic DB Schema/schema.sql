-- Job Search Tracker - Flask/MySQL application for tracking job applications,
-- detecting recruiter ghosting patterns and generating JSA evidence reports.
-- Copyright (C) 2026  Pauline A Harrison
--
-- This program is free software: you can redistribute it and/or modify
-- it under the terms of the GNU General Public License as published by
-- the Free Software Foundation, either version 3 of the License, or
-- (at your option) any later version.
--
-- This program is distributed in the hope that it will be useful,
-- but WITHOUT ANY WARRANTY; without even the implied warranty of
-- MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
-- GNU General Public License for more details.
--
-- You should have received a copy of the GNU General Public License
-- along with this program. If not, see <https://www.gnu.org/licenses/>.
--
-- Contact: info@KernEthik.com

-- ─────────────────────────────────────────────────────────────────────────────
-- Job Search Tracker - Database Schema
-- Run once: mysql -u root -p < schema.sql
-- ─────────────────────────────────────────────────────────────────────────────

CREATE DATABASE IF NOT EXISTS job_tracker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE job_tracker;

-- ─────────────────────────────────────────────
-- APPLICATIONS
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS applications (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    urn              INT UNIQUE,
    date_applied     DATE,
    source           VARCHAR(50),
    company          VARCHAR(255),
    role             VARCHAR(500),
    job_url          TEXT,
    contact_name     VARCHAR(255),
    contact_email    VARCHAR(255),
    status           VARCHAR(50) DEFAULT 'Applied',
    outcome_date     DATE,
    interview1_date  DATE,
    interview2_date  DATE,
    salary           VARCHAR(100),
    location_type    VARCHAR(100),
    notes            TEXT,
    ghost_score      INT DEFAULT 0,
    ghost_risk_tier  VARCHAR(10) DEFAULT 'None',  -- None | Medium | High | Certain
    fake_advert      TINYINT(1) DEFAULT 0,
    days_to_response INT,
    jsa_evidence     TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────
-- RECRUITER INTELLIGENCE
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recruiter_intelligence (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    company          VARCHAR(255) UNIQUE,
    type             VARCHAR(50),
    notes            TEXT,
    fake_advert_risk VARCHAR(50),
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────
-- GHOST & FAKE LOG
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ghost_fake_log (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    application_id   INT,
    date_applied     DATE,
    company          VARCHAR(255),
    role             VARCHAR(500),
    source           VARCHAR(50),
    contact_given    TINYINT(1) DEFAULT 0,
    salary_listed    TINYINT(1) DEFAULT 0,
    any_response     TINYINT(1) DEFAULT 0,
    days_since_apply INT,
    classification   VARCHAR(50),
    red_flags        TEXT,
    action_taken     TEXT,
    FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────
-- VIEWS
-- ─────────────────────────────────────────────

-- Dashboard summary counts
CREATE OR REPLACE VIEW v_dashboard AS
SELECT
    COUNT(*) AS total_applied,
    SUM(status = 'Rejected') AS rejections,
    SUM(status LIKE '%Interview%') AS interviews,
    SUM(status IN (
        'Applied',
        'Applied - Warm Contact',
        'Applied via recruiter',
        'Recruiter Call - Progressing',
        'Recruiter Call - Further Info Req'
    )) AS pending,
    SUM(
        ghost_risk_tier IN ('High', 'Certain')
        OR status IN ('No Response', 'Ghosted')
        OR ghost_score >= 3
    ) AS likely_ghosted,
    SUM(status = 'Ghosted') AS confirmed_ghosted,
    SUM(fake_advert = 1) AS fake_adverts,
    ROUND(AVG(CASE WHEN days_to_response IS NOT NULL THEN days_to_response END), 1) AS avg_response_days
FROM applications;

-- Per-recruiter ghost risk derived from application ghost_risk_tier
-- Ghost Risk column is sortable in the UI (highest tier wins per recruiter)
CREATE OR REPLACE VIEW v_recruiter_stats AS
SELECT
    a.company,
    COUNT(*) AS total_applications,
    SUM(a.outcome_date IS NOT NULL) AS total_responses,
    ROUND(100.0 * SUM(a.outcome_date IS NOT NULL) / COUNT(*), 0) AS response_rate_pct,
    SUM(
        a.ghost_risk_tier IN ('High', 'Certain')
        OR a.status IN ('No Response', 'Ghosted')
        OR a.ghost_score >= 3
    ) AS ghosted_count,
    SUM(a.fake_advert = 1) AS instant_reject_count,
    CASE
        WHEN SUM(a.fake_advert = 1) > 0                         THEN 'Certain'
        WHEN SUM(a.ghost_risk_tier = 'Certain') > 0             THEN 'Certain'
        WHEN SUM(a.status IN ('No Response', 'Ghosted')) > 0    THEN 'High'
        WHEN SUM(a.ghost_risk_tier = 'High') > 0                THEN 'High'
        WHEN SUM(a.ghost_score >= 3) > 0                        THEN 'High'
        WHEN SUM(a.ghost_risk_tier = 'Medium') > 0              THEN 'Medium'
        ELSE 'Low'
    END AS ghost_risk_rating
FROM applications a
GROUP BY a.company
ORDER BY total_applications DESC;

-- JSA evidence export (used by PDF generator and JSA tab)
CREATE OR REPLACE VIEW v_jsa_evidence AS
SELECT
    DATE_SUB(date_applied, INTERVAL WEEKDAY(date_applied) DAY) AS week_commencing,
    date_applied,
    company,
    role,
    source,
    contact_name,
    status,
    jsa_evidence,
    location_type
FROM applications
WHERE date_applied IS NOT NULL
ORDER BY date_applied DESC;
