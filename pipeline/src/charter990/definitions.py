import os

from dagster import AssetSelection, DefaultScheduleStatus, Definitions, EnvVar, ScheduleDefinition, define_asset_job

from .assets.adjudicate import adjudications
from .assets.bmf import bmf_candidates
from .assets.ccd import ccd_schools
from .assets.external import external_links
from .assets.extract import parsed_filings
from .assets.irs_index import irs_index
from .assets.match import school_matches
from .assets.related import related_links, related_orgs
from .assets.returns import raw_returns
from .assets.zips import zip_members
from .checks import (
    corrections_hold,
    fetch_failure_rate,
    known_managers_named,
    known_networks_in_pool,
    known_related_links,
    known_roles,
    match_rate,
    no_support_groups_verified,
    operating_school_count,
    parse_failure_rate,
    schema_drift,
    success_return_located,
    umbrella_watch,
)
from .resources import PostgresResource

# The IRS posts each month's e-file zips during the following month, so run on
# the 3rd. Only switched on where CHARTER990_SCHEDULE=on (the server), so a
# local `make dagster` never fires it.
refresh = define_asset_job("refresh", selection=AssetSelection.all())
monthly_refresh = ScheduleDefinition(
    name="monthly_refresh",
    job=refresh,
    cron_schedule="0 6 3 * *",
    execution_timezone="UTC",
    default_status=(
        DefaultScheduleStatus.RUNNING if os.environ.get("CHARTER990_SCHEDULE") == "on" else DefaultScheduleStatus.STOPPED
    ),
)

defs = Definitions(
    assets=[ccd_schools, external_links, bmf_candidates, irs_index, zip_members, raw_returns, parsed_filings, school_matches,
            adjudications, related_orgs, related_links],
    asset_checks=[
        operating_school_count,
        known_networks_in_pool,
        known_managers_named,
        success_return_located,
        fetch_failure_rate,
        parse_failure_rate,
        schema_drift,
        no_support_groups_verified,
        match_rate,
        corrections_hold,
        known_roles,
        umbrella_watch,
        known_related_links,
    ],
    jobs=[refresh],
    schedules=[monthly_refresh],
    resources={"pg": PostgresResource(dsn=EnvVar("DATABASE_URL"))},
)
