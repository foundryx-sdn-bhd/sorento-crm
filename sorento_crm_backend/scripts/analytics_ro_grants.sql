-- CLAUDE-CONNECTOR: grants for the read-only analytics role. Run as the schema owner after
-- `alembic upgrade head`, and again whenever the views change. Safe to repeat.
--
-- The role itself is created once by the owner, with a secret password that is never
-- committed:
--   CREATE ROLE sorento_analytics_ro LOGIN PASSWORD '<secret>'
--     NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
--
-- It gets the views and nothing else: no grant on public, chatbot or any other schema.
GRANT USAGE ON SCHEMA analytics_connector TO sorento_analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics_connector TO sorento_analytics_ro;
ALTER ROLE sorento_analytics_ro SET statement_timeout = '10s';
ALTER ROLE sorento_analytics_ro SET default_transaction_read_only = on;
