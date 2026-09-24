# References

Published work this archive's documentation relies on, with what each source does and does not
support. Cite from here rather than repeating a claim without its source.

## Ditch and canal gauging

### Cruz et al. 2019

Cruz, J.J., A.G. Fernald, D.M. VanLeeuwen, S.J. Guldan, and C.G. Ochoa. 2019. River-ditch flow
statistical relationships in a traditionally irrigated valley near Taos, New Mexico. *Journal of
Contemporary Water Research & Education* 168(1): 49-65.
DOI: [10.1111/j.1936-704X.2019.03320.x](https://doi.org/10.1111/j.1936-704x.2019.03320.x).
Open access: [opensiuc.lib.siu.edu/jcwre/vol168/iss1/7](https://opensiuc.lib.siu.edu/jcwre/vol168/iss1/7) (print issue dated December 2019; posted to the repository 2020-01-11).

**Used for:** context on how ditch discharge is commonly measured and what goes wrong, in support
of the open question about negative discharge at state ditch and canal gauges (issue
[#61](https://github.com/New-Mexico-Water/nmwater/issues/61)). See "Quality flags" in
[data-model.md](data-model.md).

**What it describes.** A study of the Rio Hondo and its eight main irrigation ditches near Taos,
March to November, 2011 to 2015. The authors installed and ran their own gauging stations.

- **Depth-based gauging.** Each ditch station had a ramp-type flume, a pressure transducer
  (Campbell Scientific CS450) and a datalogger. Discharge was not measured directly: stage from
  the transducer was converted to flow with a stage-discharge rating curve for each ditch,
  built from manual current-meter measurements taken about every two weeks.
- **Ratings go stale.** For one ditch a second rating curve was needed from August 2013, because
  changes made to the ditch "caused backwater to the measuring point".
- **Gaps from equipment failure.** Electronic failures left multi-week holes in 2015 at two
  ditches, and the authors excluded those periods rather than filling them.
- **Ditch flows are small.** Average flows reported for individual ditches ran from about 33 L/s
  (one ditch in the dry year 2013) to 424 L/s (the largest ditch over the whole record), or
  roughly 1.2 to 15 cfs. The irrigation season ran April to October, with a snowmelt peak between
  mid-May and mid-June and flow falling considerably by late July or early August.

**Why it matters here.** A depth-only device cannot sense flow direction, so a negative discharge
from such a gauge is more likely an artifact of the rating or a transducer offset than reverse
flow. And when ditch flows are only a few cfs, an offset of one or two cfs is a large fraction of
the flow, which matches the small negative values (mostly -1 to -9 cfs) seen at the state's
`ose_meas` gauges. Both points are inference from this one study.

**What it does not say.** The paper does not mention negative, zero or reverse flow, and it does
not describe the Office of the State Engineer's stations or how OSE computes ditch discharge.
It is an example of the common method, not evidence about OSE's. The seasonal pattern in our own
data (negatives most common in March and November, nearly absent at peak flow) is consistent with
it but is not shown by it.
