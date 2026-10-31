Synthetic TEI P5 descriptions in the shape e-codices publishes (`msDesc`), for
the offline tests of `eval/ecodices_bench.py` (#779). They are **not** copies of
real e-codices files: this project's cloud sessions could not reach e-codices.ch
when the bench was written (31.10.2026). The first `fetch` on tei is where the
parser meets real files; check its output against two or three descriptions read
by hand before trusting a report.
