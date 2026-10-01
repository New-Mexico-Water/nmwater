# Licence audit: what we publish and the terms of what it comes from

Date: 2026-10-01. Scope: everything in the site data bundle (`nmwater export-site-data`) and the pages built from
it: river pages (Overview, Flow, Compared with normal, Drying, Temperature and salinity, Watershed, Data notes),
their maps and share images. The reservoir fill reports and the future precipitation and reservoir pages are listed at
the end but not audited in depth. This is an engineering audit of sources and terms, not legal advice.

The question: we intend to publish our reports, charts, text and derived data files under CC BY-SA 4.0. That is
only safe if everything inside them may be redistributed commercially and re-licensed as part of our own work, so
for each input we need to know who owns it and what they allow.

## Result in one paragraph

**Nothing we publish depends on a source with a non-commercial or redistribution restriction.** CoCoRaHS, the NM
Bureau of Geology (NMBGMR), Synoptic, ZiaMet and BEMP do not feed any published number (checked in code and in the
bundle). But four inputs have **terms we could not confirm or that are unstated**, and one raises a question
beyond licensing: Colorado Division of Water Resources gauge data (12 river pages rest entirely on it), the NM State
Engineer's map layers, the Drought Monitor's permission terms, and Water Quality Portal data from state and tribal
providers (about 6% of temperature and salinity readings, about 3.4% from tribal nations). Everything else is a
U.S. government work in the public domain, or is free to reproduce with attribution (PRISM). Section 4 lists what to
do.

## 1. What feeds what

### Flow, Compared with normal, Drying (and the status on every Overview)
Gauges are merged across agencies and one value is taken per gauge and day, in this order: USGS, Reclamation
HydroData, Corps CWMS, Colorado DWR, NM State Engineer telemetry, NWS, Reclamation Albuquerque.

| Source | Gauges on published pages | Terms (as recorded; as verified) |
|---|---|---|
| USGS | 412 of 478 have a USGS copy | Public domain. Verified: "USGS-authored or produced data and information are considered to be in the U.S. Public Domain"; credit requested; third-party material excepted (usgs.gov copyrights and credits) |
| Reclamation HydroData, Corps CWMS, NWS (NWPS), Reclamation Albuquerque | 79, 72, 99, 1 | U.S. government works, public domain (17 U.S.C. 105). Not individually re-verified |
| **Colorado Division of Water Resources** | **151 gauges have a Colorado copy on 60 rivers; 42 gauges on 27 rivers have only that copy; 12 river pages have no other source** | Our catalogue says "Public (State of Colorado open data)". **Not verified: dwr.state.co.us and colorado.gov refused automated access, and the API help page states no terms.** The 12 rivers are the Dolores, Piedra (East and Middle Forks), Pinos Creek, Rock Creek, Goose, Basin, Cascade, Disappointment, Beaver and San Francisco Creeks and Willow Creek (Rio Grande headwaters) |
| NM State Engineer telemetry (`ose_meas`) | none | Not used: its stations are ditches and acequias, which the pages exclude. Checked: no `ose_meas` copy on any published gauge |

Weekly means, normals, percentiles and dry-day counts are our computations on these values; the individual daily
values are also in the CSV downloads, so Colorado values are redistributed (as weekly means by gauge and segment).

### Overview content
| Content | Source | Terms |
|---|---|---|
| Dams and reservoirs table | National Inventory of Dams (Corps), our reservoir registry (`catalog/reservoirs.yaml`, our own notes) | Federal public domain; ours |
| Towns, counties, state outline, urban areas | Census TIGER | Public domain |
| River lines, tributaries, waterbodies, drainage areas | NHDPlus v2 (USGS/EPA), WBD watersheds | Public domain |
| **Acequias along the river** | NM State Engineer `Conveyances` layer. Its credit line: "OSE GIS, EDAC, NMSU, USGS, NM Acequia Association". Our list is acequia **names** within 1.5 km of the river | **No licence stated** (service metadata `licenseInfo` empty). Several contributing parties (UNM's Earth Data Analysis Center, NMSU, USGS, the Acequia Association) |
| **Irrigation districts** | State Engineer `NM_Irrigation_Districts`. Credit line: two individuals | **No licence stated** |
| **County water use, 2020** | State Engineer `water_use_2020` | **No licence or description stated** (service metadata empty) |
| **Public water systems** | State Engineer `New_Mexico_Public_Water_Systems`. Credit: "Office of the State Engineer Water Use & Conservation Bureau" | **No licence stated** |
| Acequias with evidence of self-governance | State DFA ICIP filings (public records), and each acequia's or association's own site; our summary and a list of names | Facts and names; our own wording; sources linked |
| Habitat, culture, background text | Our own wording, written from the cited pages (agency, tribal, museum, university and news sources); every statement was checked against its source and is a paraphrase, not a copy | Ours. The cited pages keep their own copyright |

### Temperature and salinity
Readings come from every stream-snapped sensor or sample on the river: 2,230 Water Quality Portal sites, 70 USGS sites
and one Corps site across the candidate rivers; 993,118 temperature and conductance readings. By provider:

| Provider | Share of readings |
|---|---|
| USGS (direct 51.4%, and via the Portal 11.8%) | 63.2% |
| National Park Service Water Resources Division | 18.0% |
| Corps CWMS | 12.8% |
| **Federal subtotal** | **94.0%** |
| NM Environment Department | 1.2% |
| Colorado Dept. of Public Health and Environment | 0.6% |
| Arizona DEQ | 0.2% |
| **Tribal nations** (Southern Ute Tribe 1.3%, Pueblo of Taos 0.9%; Pueblo of Tesuque, Pueblo of Santa Clara, Ohkay Owingeh Pueblo and Ute Mountain Utes Tribe about 0.2% or less each; plus codes `SANDIAWQ`, `NAMBEPBLO` and `PUEBLO` that the Portal's organisation list does not name: the first two are presumably Sandia and Nambe, the last is unidentified) | **about 3.4%** |
| Other small providers | about 0.6% |

Provider names are from the Portal's own organisation list (checked 2026-10-01) except the three codes noted. The Water Quality Portal states no licence: its front page and user guide say only that it integrates "publicly
available" data from USGS, EPA and "over 400 state, federal, tribal, and local agencies" (checked 2026-10-01).

### Watershed
| Content | Source | Terms |
|---|---|---|
| Precipitation, air temperature | PRISM, Oregon State University | Verified (prism.oregonstate.edu/terms): OSU retains ownership; "All data ... may be freely reproduced and distributed"; "any description should clearly and prominently state, at a minimum, our name, URL, and the date of data access"; map graphics need the PRISM copyright notice, URL and map date. No commercial restriction stated; nothing on derived products. We publish watershed averages, not grids |
| Snow-water equivalent | SNODAS (NOAA NOHRSC, via NSIDC) | Public domain |
| Drought index | U.S. Drought Monitor (NDMC, USDA, NOAA) | Our catalogue: "Public; cite the U.S. Drought Monitor". **Not verified: the permission page returned no readable terms** |

### Not in anything published
CoCoRaHS (non-commercial), NM Bureau of Geology (non-commercial), Synoptic (non-commercial, redistribution
restricted), ZiaMet (no terms stated), BEMP (cite BEMP), gridMET, UA SWE, EDI and IEM DCP. CoCoRaHS was used once, to check PRISM's day alignment; no number from it is published.

## 2. Findings

1. **Colorado data (amber).** The only unverified input that carries whole pages. Terms may well be open, but we have
   not read them, and 12 rivers cannot be shown without it.
2. **State Engineer layers (amber).** Four layers published with no licence and credit lines that name several
   parties. Public records law gives a right to inspect and copy, not a licence, and the federal public-domain rule
   does not cover states. We publish names and counts derived from them (acequia names, district names and areas,
   county water-use totals, system names), which are largely facts, but the acequia layer's compilers are a university,
   a nonprofit and several agencies.
3. **Drought Monitor (amber, small).** We publish the Drought Severity and Coverage Index for each segment's HUC4 basin.
   The permission terms were not readable.
4. **Water Quality Portal providers (amber, and an ethics question).** No licence stated. About 6% of readings are
   from state, tribal and out-of-state providers. About 3.4% are from tribal nations' own monitoring programs.
   Those programs chose to submit the data to a public federal system, so the data are public, but "public" is not the
   same as "we should republish derived statistics without telling the data's stewards": tribal data sovereignty
   practice (the CARE principles: collective benefit, authority to control, responsibility, ethics) asks for more.
   Credit is the minimum; notifying the programs and offering to remove their data from our medians is the careful
   course. This is a judgement call for the project owner.
5. **PRISM attribution is specific (green, with a duty).** Every page that shows PRISM-derived values must state
   PRISM's name, its URL and the date the data were accessed, prominently, not only in a footer.
6. **No red items.** No input restricts commercial use or redistribution, so CC BY-SA 4.0 for our own contribution is
   not blocked by anything we found.

## 3. What CC BY-SA 4.0 can and cannot say here

It licenses what we contribute: our selection and arrangement, computations, text, charts and maps as drawn. It does
not relicense upstream data and cannot grant rights we do not hold. Raw facts are not copyrightable in the United
States, so for the data files the licence mostly expresses attribution and share-alike intent. (In the EU it also
covers database rights.) The licence text says this plainly in `LICENSING.md` and in the About page, and every page
lists its sources.

## 4. Actions, in order

1. **Read Colorado's terms and the Drought Monitor's permission page by hand** (a browser works where automated
   access did not), or email Colorado DWR / CDSS and the NDMC. Until confirmed, decide whether the 12 Colorado-only
   river pages publish.
2. **Ask the State Engineer's office for a one-line reuse statement** covering the acequia, irrigation district,
   water-use and public-water-system layers, and name the Acequia Association and UNM EDAC in the credit. Fallback
   if refused: publish acequia names with the layer credited and drop what they decline.
3. **Decide the tribal-data question** (finding 4). Recommended: credit the providers on the Data notes and About
   pages, tell the tribal environmental programs, and remove any program's data from our medians if asked.
4. **Put the PRISM credit** (name, URL, date accessed) in every Watershed tab and on the About page. The About-page
   draft in the site repository already does.
5. **Re-run this audit** when a source is added to the bundle or the flow source order changes. The method is
   below; there is deliberately no automated test.

## 5. Method (so it can be repeated)
- Gauge sources: every `flow.json` in the bundle lists each gauge's copies; counted by source, and gauges with no
  federal copy listed (`dist/site-data/rivers/*/flow.json`, field `gauges[].copies`).
- Quality sources: for every published river, `river_quality.read_quality_sites` gives the candidate sensors; readings
  of `water_temp` and `specific_conductance` from `observations_clean` were counted by site owner (the organisation
  code in a Water Quality Portal site id, or the source).
- Overview sources: read from `river_context.py`, `river_overview.py` and `river_map.py`.
- Terms: PRISM (prism.oregonstate.edu/terms) and USGS (usgs.gov/information-policies-and-instructions/copyrights-and-credits)
  read 2026-10-01; State Engineer ArcGIS service metadata (`copyrightText`, `licenseInfo`) read 2026-10-01 from
  services2.arcgis.com/qXZbWTdPDbTjl7Dy; Water Quality Portal user guide read 2026-10-01; Colorado and the Drought
  Monitor unreadable as described.

## 6. Not yet audited
- Reservoir fill reports (`docs/reports/reservoirs/`): the registry (`catalog/reservoirs.yaml`) names USGS, Reclamation and
  Corps series and the dam inventory; none of the restricted sources and not IEM DCP. Not checked series by series.
- Watershed precipitation dataset (`watershed_precip`): PRISM only, so the PRISM terms above apply.
- The site's own assets: Public Sans (SIL Open Font License), shadcn-svelte, bits-ui, Astro and Svelte (MIT).
