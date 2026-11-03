"""Series that Reclamation lists but no longer serves are skipped, not requested every night (#43)."""

import pandas as pd

from nmwater.sources.usbr_hydrodata import drop_retired


def test_retired_series_are_dropped_and_counted_and_nothing_else():
    meta = pd.DataFrame({"category": ["gage_data", "gage_data", "reservoir_data"], "site_id": ["1536", "1536", "2685"], "datatype_id": ["20", "19", "43"]})
    out, n = drop_retired(meta, ["gage_data/1536/20", "reservoir_data/2685/43"])
    assert n == 2 and out.datatype_id.tolist() == ["19"]
    assert drop_retired(meta, [])[1] == 0 and len(drop_retired(meta, None)[0]) == 3


def test_the_config_lists_the_32_series_that_returned_404():
    import yaml
    from nmwater.core.config import PROJECT_ROOT
    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "sources.yaml").read_text())["sources"]["usbr_hydrodata"]
    assert len(cfg["retired"]) == 32 and all(k.count("/") == 2 for k in cfg["retired"])
