-- Website domains of regular school districts and other non-charter agencies,
-- from the NCES district directory. A school or nonprofit listing one of these
-- (for example baltimorecityschools.org) is pointing at the district, which says
-- nothing about which nonprofit runs a charter school.
CREATE TABLE IF NOT EXISTS district_domains (
    domain  text PRIMARY KEY
);
