-- The 23 ANALYTICS tables and the audit table. Columns checked one by one against
-- the SQLite seed: no table or column is missing on either side.
--
-- infra/snowflake/load.py runs these as CREATE OR REPLACE, so a reload starts
-- clean. Only primary keys are declared, and Snowflake does not enforce those
-- either, so load.py checks keys and foreign-key orphans itself, using the
-- SQLite seed's foreign keys.

CREATE TABLE ANALYTICS.INTAKE_CHANNELS (
    channel_id NUMBER(38,0) PRIMARY KEY,
    channel_code VARCHAR NOT NULL,          -- web, mobile_app, ivr, social, email, walk_in
    channel_name VARCHAR NOT NULL,
    text_quality_band VARCHAR NOT NULL,     -- high, medium, low
    typical_severity_accuracy FLOAT NOT NULL
);

CREATE TABLE ANALYTICS.SERVICE_CATEGORIES (
    category_id NUMBER(38,0) PRIMARY KEY,
    category_code VARCHAR NOT NULL,
    category_name VARCHAR NOT NULL,
    category_group VARCHAR NOT NULL,        -- Streets, Sanitation, Parks, Code, Utilities, Animal, Noise, Other
    default_priority VARCHAR NOT NULL,      -- low, medium, high, emergency
    is_other NUMBER(38,0) NOT NULL DEFAULT 0
);

CREATE TABLE ANALYTICS.TENANTS (
    tenant_id NUMBER(38,0) PRIMARY KEY,
    tenant_code VARCHAR NOT NULL,
    tenant_name VARCHAR NOT NULL,
    state_or_province VARCHAR NOT NULL,
    country VARCHAR NOT NULL,               -- US or CA
    population NUMBER(38,0) NOT NULL,
    size_tier VARCHAR NOT NULL,             -- small, medium, large
    timezone VARCHAR NOT NULL,
    vocab_drift_level VARCHAR NOT NULL,     -- low, medium, high
    has_french_vocab NUMBER(38,0) NOT NULL DEFAULT 0,
    launched_on DATE NOT NULL
);

CREATE TABLE ANALYTICS.TENANT_SUBSCRIPTIONS (
    subscription_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    contract_start_date DATE NOT NULL,
    contract_end_date DATE NOT NULL,
    arr_usd FLOAT NOT NULL,
    has_ai_insight_pack NUMBER(38,0) NOT NULL,
    has_field_mobile_pack NUMBER(38,0) NOT NULL,
    has_analytics_pack NUMBER(38,0) NOT NULL,
    is_active NUMBER(38,0) NOT NULL
);

CREATE TABLE ANALYTICS.DEPARTMENTS (
    dept_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    dept_code VARCHAR NOT NULL,
    dept_name VARCHAR NOT NULL,
    dept_type VARCHAR NOT NULL,             -- streets, sanitation, ... general_queue, other
    is_general_queue NUMBER(38,0) NOT NULL DEFAULT 0
);

CREATE TABLE ANALYTICS.SLA_POLICIES (
    sla_policy_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    category_id NUMBER(38,0) NOT NULL,      -- FK SERVICE_CATEGORIES
    target_business_days NUMBER(38,0) NOT NULL,
    escalation_business_days NUMBER(38,0) NOT NULL,
    effective_from DATE NOT NULL
);

CREATE TABLE ANALYTICS.CATEGORY_DEPT_ROUTING (
    routing_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    category_id NUMBER(38,0) NOT NULL,      -- FK SERVICE_CATEGORIES
    dept_id NUMBER(38,0) NOT NULL,          -- FK DEPARTMENTS
    is_primary NUMBER(38,0) NOT NULL DEFAULT 1,
    effective_from DATE NOT NULL
);

CREATE TABLE ANALYTICS.CITIZENS (
    citizen_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    citizen_external_id VARCHAR NOT NULL,
    first_name VARCHAR NOT NULL,            -- PII sensitive
    last_name VARCHAR NOT NULL,             -- PII sensitive
    email VARCHAR,                          -- PII sensitive
    phone VARCHAR,                          -- PII sensitive
    registered_on DATE NOT NULL,
    preferred_channel_id NUMBER(38,0)       -- FK INTAKE_CHANNELS
);

CREATE TABLE ANALYTICS.ADDRESSES (
    address_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    street_address VARCHAR NOT NULL,
    city VARCHAR NOT NULL,
    state_or_province VARCHAR NOT NULL,
    postal_code VARCHAR NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    neighborhood VARCHAR
);

CREATE TABLE ANALYTICS.ASSETS (
    asset_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    address_id NUMBER(38,0),                -- FK ADDRESSES, nullable
    asset_type VARCHAR NOT NULL,            -- streetlight, hydrant, intersection, trash_can, sign, bench
    asset_external_id VARCHAR NOT NULL,
    installed_on DATE
);

CREATE TABLE ANALYTICS.STAFF_USERS (
    staff_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    dept_id NUMBER(38,0) NOT NULL,          -- FK DEPARTMENTS
    staff_name VARCHAR NOT NULL,
    role VARCHAR NOT NULL,                  -- call_taker, supervisor, inspector, manager, director, crew_lead
    email VARCHAR,
    hired_on DATE,
    is_active NUMBER(38,0) NOT NULL DEFAULT 1
);

CREATE TABLE ANALYTICS.FIELD_CREWS (
    crew_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    dept_id NUMBER(38,0) NOT NULL,          -- FK DEPARTMENTS
    crew_code VARCHAR NOT NULL,
    crew_size NUMBER(38,0) NOT NULL,
    shift_type VARCHAR NOT NULL             -- day, night, swing
);

CREATE TABLE ANALYTICS.SERVICE_REQUESTS (
    request_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    citizen_id NUMBER(38,0),                -- FK CITIZENS, nullable (anonymous)
    address_id NUMBER(38,0),                -- FK ADDRESSES, nullable
    asset_id NUMBER(38,0),                  -- FK ASSETS, nullable
    auto_category_id NUMBER(38,0) NOT NULL, -- FK SERVICE_CATEGORIES (system landed category)
    channel_id NUMBER(38,0) NOT NULL,       -- FK INTAKE_CHANNELS
    routed_dept_id NUMBER(38,0),            -- FK DEPARTMENTS, nullable
    stated_severity VARCHAR NOT NULL,       -- low, medium, high, emergency
    current_status VARCHAR NOT NULL,        -- open, classified, assigned, in_progress, on_hold, closed, reopened
    created_at TIMESTAMP_NTZ NOT NULL,
    closed_at TIMESTAMP_NTZ,                -- nullable (not closed)
    is_duplicate NUMBER(38,0) NOT NULL DEFAULT 0,
    duplicate_of_request_id NUMBER(38,0)    -- FK SERVICE_REQUESTS, nullable
);

CREATE TABLE ANALYTICS.REQUEST_DESCRIPTIONS (
    description_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS, UNIQUE 1:1
    description_text VARCHAR NOT NULL,      -- resident free text (trap 1/2/4 signal source)
    description_length NUMBER(38,0) NOT NULL,
    submitted_via_text_field NUMBER(38,0) NOT NULL  -- 1 typed, 0 IVR transcribed
);

CREATE TABLE ANALYTICS.REQUEST_EVENTS (
    event_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS
    event_type VARCHAR NOT NULL,            -- created, classified, assigned, in_progress, on_hold, closed, reopened
    event_at TIMESTAMP_NTZ NOT NULL,
    by_staff_id NUMBER(38,0),               -- FK STAFF_USERS, nullable
    event_note VARCHAR
);

CREATE TABLE ANALYTICS.REQUEST_ASSIGNMENTS (
    assignment_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS
    dept_id NUMBER(38,0) NOT NULL,          -- FK DEPARTMENTS
    staff_id NUMBER(38,0),                  -- FK STAFF_USERS, nullable
    crew_id NUMBER(38,0),                   -- FK FIELD_CREWS, nullable
    assigned_at TIMESTAMP_NTZ NOT NULL,
    unassigned_at TIMESTAMP_NTZ,
    is_current NUMBER(38,0) NOT NULL DEFAULT 1
);

CREATE TABLE ANALYTICS.REQUEST_ATTACHMENTS (
    attachment_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS
    file_type VARCHAR NOT NULL,             -- image, video, document
    file_name VARCHAR NOT NULL,
    uploaded_at TIMESTAMP_NTZ NOT NULL
);

CREATE TABLE ANALYTICS.WORK_ORDERS (
    work_order_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS
    dept_id NUMBER(38,0) NOT NULL,          -- FK DEPARTMENTS
    crew_id NUMBER(38,0),                   -- FK FIELD_CREWS, nullable
    supervisor_staff_id NUMBER(38,0),       -- FK STAFF_USERS, nullable
    opened_at TIMESTAMP_NTZ NOT NULL,
    closed_at TIMESTAMP_NTZ,
    outcome VARCHAR,                        -- resolved, deferred, no_action, unable_to_locate
    structured_fail_code VARCHAR            -- unable_to_access, materials_missing, weather, deferred_to_capital, no_issue_found, NULL
);

CREATE TABLE ANALYTICS.WORK_ORDER_NOTES (
    note_id NUMBER(38,0) PRIMARY KEY,
    work_order_id NUMBER(38,0) NOT NULL,    -- FK WORK_ORDERS
    by_staff_id NUMBER(38,0) NOT NULL,      -- FK STAFF_USERS
    note_at TIMESTAMP_NTZ NOT NULL,
    note_text VARCHAR NOT NULL,             -- free text (trap 3 secondary evidence)
    note_type VARCHAR NOT NULL              -- status_update, fail_reason, citizen_followup, internal
);

CREATE TABLE ANALYTICS.MODEL_PREDICTIONS (
    prediction_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS, UNIQUE 1:1
    predicted_category_id NUMBER(38,0) NOT NULL,  -- FK SERVICE_CATEGORIES (raw top1)
    confidence_score FLOAT NOT NULL,        -- 0..1; <0.45 => predicted = Other (fallback)
    predicted_at TIMESTAMP_NTZ NOT NULL,
    ground_truth_category_id NUMBER(38,0),  -- FK SERVICE_CATEGORIES, nullable (audited subset only)
    is_audited NUMBER(38,0) NOT NULL DEFAULT 0,
    model_version VARCHAR NOT NULL
);

CREATE TABLE ANALYTICS.SLA_BREACH_LOG (
    breach_id NUMBER(38,0) PRIMARY KEY,
    request_id NUMBER(38,0) NOT NULL,       -- FK SERVICE_REQUESTS, UNIQUE 1:1 (only breached requests)
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    breached_at TIMESTAMP_NTZ NOT NULL,
    target_business_days NUMBER(38,0) NOT NULL,
    actual_business_days NUMBER(38,0) NOT NULL,
    breach_business_days NUMBER(38,0) NOT NULL,  -- actual - target, >= 1
    breach_severity VARCHAR NOT NULL        -- minor, major, critical
);

CREATE TABLE ANALYTICS.CITIZEN_COMPLAINTS (
    complaint_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    citizen_id NUMBER(38,0) NOT NULL,       -- FK CITIZENS
    primary_request_id NUMBER(38,0) NOT NULL,  -- FK SERVICE_REQUESTS
    filed_at DATE NOT NULL,
    escalation_channel VARCHAR NOT NULL,    -- council_letter, news_media, lawyer_letter, social_viral
    complaint_narrative VARCHAR NOT NULL,   -- free text
    council_member_name VARCHAR,
    resolved_at DATE,
    resolution_type VARCHAR                 -- apology, repair_completed, policy_change, no_action
);

CREATE TABLE ANALYTICS.TENANT_HEALTH_SNAPSHOTS (
    snapshot_id NUMBER(38,0) PRIMARY KEY,
    tenant_id NUMBER(38,0) NOT NULL,        -- FK TENANTS
    snapshot_month DATE NOT NULL,           -- first day of month
    request_count NUMBER(38,0) NOT NULL,
    sla_breach_rate FLOAT NOT NULL,
    reopen_rate FLOAT NOT NULL,
    repeat_at_address_rate FLOAT NOT NULL,
    classifier_accuracy FLOAT,              -- nullable (NULL for tenants without AI Insight Pack)
    council_escalation_rate FLOAT NOT NULL,
    nps_score NUMBER(38,0) NOT NULL,        -- -100..+100
    churn_risk_band VARCHAR NOT NULL        -- low, medium, high
);

CREATE TABLE CITYPULSE_AUDIT.QUERY_LOG (
    log_id VARCHAR PRIMARY KEY,             -- uuid
    session_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    asked_at TIMESTAMP_NTZ NOT NULL,
    nl_query VARCHAR NOT NULL,
    generated_sql VARCHAR,
    row_count NUMBER(38,0),                 -- count only, no values
    tool_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,                -- ok, sql_error, timeout, empty, blocked
    latency_ms NUMBER(38,0)
);
