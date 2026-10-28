-- Read-only access for the website, applied by make neon-reader (which creates
-- the role in SQL) and make neon-roles. The website connects as web_reader, so
-- a leaked website setting can read public data but never change it. Don't
-- create the role in the Neon console or CLI: those roles join neon_superuser
-- and can write every table.
GRANT USAGE ON SCHEMA public TO web_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO web_reader;
-- Tables and views the pipeline creates later are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO web_reader;
