from nmwater.sources.nwps import flood_row


def test_flood_row_reads_the_four_categories_and_their_units():
    d = {"flood": {"stageUnits": "ft", "flowUnits": "cfs", "categories": {
        "major": {"stage": 12, "flow": 35000}, "moderate": {"stage": 10, "flow": 21500},
        "minor": {"stage": 8.5, "flow": 14400}, "action": {"stage": 7, "flow": 8535}}}}
    r = flood_row("FRMN5", "nwps:FRMN5", d)
    assert r["site_uid"] == "nwps:FRMN5" and r["stage_units"] == "ft" and r["flow_units"] == "cfs"
    assert (r["action_stage"], r["minor_stage"], r["moderate_stage"], r["major_stage"]) == (7, 8.5, 10, 12)
    assert (r["action_flow"], r["major_flow"]) == (8535, 35000)


def test_undefined_levels_are_missing_not_minus_999():
    d = {"flood": {"stageUnits": "ft", "flowUnits": "kcfs", "categories": {"minor": {"stage": 6, "flow": -999}, "major": {"stage": -999, "flow": -999}}}}
    r = flood_row("X", "nwps:X", d)
    assert r["minor_stage"] == 6 and r["minor_flow"] is None and r["major_stage"] is None and r["action_stage"] is None


def test_a_gauge_with_no_categories_has_no_row():
    assert flood_row("X", "nwps:X", {}) is None
    assert flood_row("X", "nwps:X", {"flood": {"categories": {"minor": {"stage": -999, "flow": -999}}}}) is None
