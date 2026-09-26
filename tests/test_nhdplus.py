"""River naming for sites on unnamed NHD reaches."""

import numpy as np
import pandas as pd

from nmwater.sources.nhdplus import _name, downstream_names


def _net(rows):
    return pd.DataFrame(rows, columns=["comid", "gnis_name", "fromnode", "tonode", "divergence"])


def test_unnamed_reaches_take_the_first_named_river_downstream():
    #  1 (unnamed) -> 2 (unnamed) -> 3 "Rio Chama" -> 4 "Rio Grande"
    a = _net([(1, " ", 10, 20, 0), (2, np.nan, 20, 30, 0), (3, "Rio Chama", 30, 40, 0), (4, "Rio Grande", 40, 50, 0)])
    d = downstream_names(a)
    assert d[1] == ("Rio Chama", 2) and d[2] == ("Rio Chama", 1)
    assert 3 not in d                                    # named reaches keep their own name


def test_divergence_follows_the_main_path_and_dead_ends_stay_unnamed():
    # node 20 splits: 3 is the minor branch (divergence 2), 2 the main path
    a = _net([(1, None, 10, 20, 0), (3, "Ditch Creek", 20, 60, 2), (2, "Pecos River", 20, 30, 1),
              (5, None, 70, 80, 0)])                     # 5 drains nowhere named (closed basin)
    d = downstream_names(a)
    assert d[1] == ("Pecos River", 1)
    assert d[5] == (None, None)


def test_name_repairs_nhd_enye_corruption():
    assert _name("Ca¿ones Creek") == "Cañones Creek"
    assert _name(" ") is None and _name(np.nan) is None
