from dagster import Definitions, EnvVar

from .assets.bmf import bmf_candidates
from .assets.ccd import ccd_schools
from .assets.extract import parsed_filings
from .assets.irs_index import irs_index
from .assets.match import school_matches
from .assets.returns import raw_returns
from .assets.zips import zip_members
from .checks import (
    corrections_hold,
    fetch_failure_rate,
    known_networks_in_pool,
    known_roles,
    match_rate,
    no_support_groups_verified,
    operating_school_count,
    success_return_located,
    umbrella_watch,
)
from .resources import PostgresResource

defs = Definitions(
    assets=[ccd_schools, bmf_candidates, irs_index, zip_members, raw_returns, parsed_filings, school_matches],
    asset_checks=[
        operating_school_count,
        known_networks_in_pool,
        success_return_located,
        fetch_failure_rate,
        no_support_groups_verified,
        match_rate,
        corrections_hold,
        known_roles,
        umbrella_watch,
    ],
    resources={"pg": PostgresResource(dsn=EnvVar("DATABASE_URL"))},
)
