-- Runs once, on first start of the local Postgres container.
-- Creates the test database and enables PostGIS in both databases.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE DATABASE spm_test OWNER spm;
\connect spm_test
CREATE EXTENSION IF NOT EXISTS postgis;
