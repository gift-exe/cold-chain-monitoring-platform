#!/bin/bash
# Runs once, when the database volume is first created.
# coldchain_owner owns the schema and runs migrations; coldchain_app is the role the services use,
# which is given no UPDATE or DELETE rights on the append-only tables (see migration 0001).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE ROLE coldchain_owner LOGIN PASSWORD '${COLDCHAIN_DB_PASSWORD}';
CREATE ROLE coldchain_app LOGIN PASSWORD '${COLDCHAIN_APP_PASSWORD}';
CREATE DATABASE coldchain OWNER coldchain_owner;

CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
CREATE DATABASE keycloak OWNER keycloak;
SQL

# Extensions must be created by a superuser
psql -v ON_ERROR_STOP=1 --username postgres --dbname coldchain <<SQL
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS btree_gist;
REVOKE ALL ON DATABASE coldchain FROM PUBLIC;
GRANT CONNECT ON DATABASE coldchain TO coldchain_app;
SQL
