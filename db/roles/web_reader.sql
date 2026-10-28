-- Read-only access for the website. Create the role first in the Neon console
-- (Roles > New role: web_reader), which generates its password, then run this
-- as the owner: make neon-roles. The website connects as web_reader, so a
-- leaked website setting can read public data but never change it.
GRANT USAGE ON SCHEMA public TO web_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO web_reader;
-- Tables and views the pipeline creates later are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO web_reader;
