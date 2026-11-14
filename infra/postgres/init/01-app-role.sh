#!/bin/sh
# Runs once, on first initialisation of the data volume. Creates the application login
# role: NOSUPERUSER and NOBYPASSRLS so row-level security always applies (ADR-0006).
# Migrations (as the owner role) grant it privileges.
#
# On an existing volume this does not run again; migration 0001 would then create
# mera_app as NOLOGIN. Reset with: docker compose down -v (destroys local data).
set -eu
: "${MERA_APP_DB_PASSWORD:?MERA_APP_DB_PASSWORD must be set}"
# Password passed as a psql variable and quoted with :'pw', so quotes in it are safe.
psql -v ON_ERROR_STOP=1 -v pw="$MERA_APP_DB_PASSWORD" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE mera_app LOGIN NOSUPERUSER NOBYPASSRLS;
ALTER ROLE mera_app PASSWORD :'pw';
SQL
