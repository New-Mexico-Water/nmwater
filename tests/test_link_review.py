"""Rules that decide which automatic `colocated` links the evidence settles."""

from nmwater.catalog.link_review import Agreement, agrees, decide, name_match, same_name


def site(uid, name):
    return {"site_uid": uid, "name": name, "source": uid.split(":")[0]}


def test_names_match_on_the_place_through_abbreviations_and_punctuation():
    assert name_match("Pecos River bl Taiban Creek", "PECOS RIVER BELOW TAIBAN CK, NM") == 1.0
    assert name_match("Rio Penasco at Dayton", "RIO PENASCO AT DAYTON N M") == 1.0
    assert name_match("Santa Cruz Dam", "Santa Cruz Lake (deep) - 28SantaCruzDp") < 0.66


def test_a_name_above_a_place_does_not_match_one_below_it():
    assert name_match("Rio Chama above Abiquiu", "Rio Chama below Abiquiu") == 0.0


def test_a_computed_series_is_not_the_gauge():
    assert name_match("PECOS RIVER NEAR ARTESIA, NM", "PECOS R NR ARTESIA PLUS PECOS PUMPAGE") < 0.66


def test_agreement_needs_days_a_tight_difference_and_correlation():
    assert agrees(Agreement("stage", 66, 0.0, 0.9999))
    assert not agrees(Agreement("stage", 30, 0.0, 0.9999))          # too few days
    assert not agrees(Agreement("stage", 66, 0.02, 0.9999))         # 2% is not the same record
    assert not agrees(Agreement("stage", 66, 0.0, 0.95))
    assert not agrees(Agreement("water_temp", 500, 0.0, 1.0))       # only gauge variables count
    assert agrees(Agreement("discharge", 90, 0.02, 0.995)) and not agrees(Agreement("discharge", 90, 0.06, 0.995))


def test_identical_stage_makes_same_sensor_and_notes_the_ratings_that_differ():
    d = decide(site("nwps:ACMN5", "Pecos River below Acme"), site("usace_cwms:Acme", "Pecos River nr Acme"), 0.0,
               [Agreement("stage", 66, 0.0, 0.99998), Agreement("discharge", 66, 0.10, 0.9993)])
    assert d[0] == "same_sensor" and "stage" in d[1] and "own rating" in d[1]


def test_equal_station_numbers_need_matching_names():
    ok = decide(site("ose_arcgis:rtm:25", "Acequia Larga"), site("ose_meas:25", "Larga"), 0.0, [])
    assert ok and ok[0] == "same_sensor"
    assert decide(site("ose_arcgis:rtm:37", "Ensenada"), site("ose_meas:37", "Porvenir"), 0.0, []) is None
    assert same_name("Left Hand", "Lefthand") and not same_name("Brown", "Wanda Haley Rhodes")


def test_a_sampling_point_at_a_gauge_is_the_same_place_not_the_same_sensor():
    d = decide(site("usgs:08382000", "GALLINAS RIVER NEAR LOURDES, NM"), site("wqp:USGS-08382000", "Gallinas River nr Lourdes"), 40.0, [])
    assert d[0] == "same_location_different_sensor"
    assert decide(site("usgs:08382000", "GALLINAS RIVER NEAR LOURDES, NM"), site("wqp:X", "Gallinas River nr Lourdes"), 180.0, []) is None   # too far


def test_two_gauge_feeds_with_no_series_evidence_must_be_very_close():
    a, b = site("codwr:A", "Rio Chama near La Puente"), site("usace_cwms:B", "RIO CHAMA NR LA PUENTE")
    assert decide(a, b, 10.0, [])[0] == "same_location_different_sensor"
    assert decide(a, b, 60.0, []) is None


def test_neighbours_and_unrelated_names_stay_candidates():
    assert decide(site("nwps:BWLN5", "Bluewater Creek at Bluewater Reservoir"), site("wqp:USGS-1", "RSJ Spillway from Bluewater Lake"), 47.7, []) is None
    assert decide(site("usgs:1", "A"), site("usgs:2", "A"), 0.0, []) is None          # one source never links to itself


from nmwater.catalog.link_review import decide_well


def test_equal_depth_at_the_same_spot_is_one_well_in_two_programmes():
    d = decide_well(site("nmwdi_st2:9767", "Columbine Thompson 07"), site("wqp:USGS-350726106223101", "10N.05E.02.141A"), 6.0, 48.768, 48.768)
    assert d and d[0] == "same_location_different_sensor" and "Same well" in d[1]


def test_nested_piezometers_at_different_depths_are_different_wells():
    assert decide_well(site("nmwdi_st2:9094", "WBLUFFN1P3"), site("wqp:USGS-350638106413704", "WEST BLUFF NO. 2"), 0.2, 133.2, 99.97) is None


def test_a_wqp_id_that_carries_the_usgs_station_number_names_the_same_well():
    a, b = site("usgs:341525102435405", "AR-10-43-705"), site("wqp:TX001-341525102435405", "AR-10-43-705")
    d = decide_well(a, b, 0.0, 27.432, 27.432)
    assert d and "341525102435405" in d[1]
    assert decide_well(a, b, 0.0, 27.4, 40.0) is None                 # the depths disagree: not that well


def test_wells_far_apart_or_with_no_depth_stay_candidates():
    assert decide_well(site("usgs:1", "A"), site("wqp:X", "A"), 40.0, 30.0, 30.0) is None
    assert decide_well(site("usgs:1", "A"), site("wqp:X", "A"), 2.0, None, 30.0) is None
