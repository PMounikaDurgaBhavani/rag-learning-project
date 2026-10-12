# Invalid outputs — kept as evidence, not results

`result_20260915_UNLABELLED_scored-2026-10-12T0612Z.json` was written by
`week5_prediction.py score` at 2026-10-12T06:12Z while all 20 traces in
`notes_20260915.md` were still unreviewed (`"unlabelled"` lists all 20).
Its counts (M1 0/20, correct refusals 0/20) are zeros by construction, not
measurements, and its `prediction_holds` is `null`. It is NOT a Week 5 result.

The bug that let it be written is fixed: `score` now refuses to write
`result_<seed>.json` and exits 2 while any trace lacks an `answerable` or
`modes` line (`tests/test_week5_prediction.py` covers blank and partial reviews).
