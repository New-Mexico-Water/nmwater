"""Constants shared across sources."""

# New Mexico's 33 county FIPS codes, from the Census Bureau county boundaries (TIGER/Line).
# Every code is odd except Cibola (35006, created 1981) and Los Alamos (35028). Four source
# modules once generated the list as range(1, 62, 2), which is odd numbers only, so they silently
# skipped both counties (found 2026-09-24). Import this instead of computing the list.
NM_COUNTY_FIPS: tuple[str, ...] = (
    "35001", "35003", "35005", "35006", "35007", "35009", "35011", "35013", "35015", "35017", "35019",
    "35021", "35023", "35025", "35027", "35028", "35029", "35031", "35033", "35035", "35037", "35039",
    "35041", "35043", "35045", "35047", "35049", "35051", "35053", "35055", "35057", "35059", "35061",
)
