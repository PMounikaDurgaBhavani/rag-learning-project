# Week 6 — Judge V1 vs V2 Comparison

## Results

- Frozen human labels: 25
- Judge v1 agreement: 14/25 (56.0%)
- Judge v1 Cohen's kappa: -0.141
- Judge v2 agreement: 19/25 (76.0%)
- Judge v2 Cohen's kappa: 0.409
- Judge v2 held-out agreement: 18/23 (78.3%)
- Agreement improvement: +20 percentage points
- Disagreements reduced: 11 to 6

## What changed?

Judge v2 used T02 and T13 as worked examples. Overall agreement
increased from 56.0% to 76.0%. Held-out agreement was 78.3%,
so the improvement was not limited to the two example cases.

## Remaining disagreements

- T02: False PASS — important password-reset instructions were omitted.
- T06: False FAIL — reasonable upload-permission guidance was rejected.
- T08: False PASS — unsupported upload limits and formats were accepted.
- T11: False PASS — essential password-reset steps were missing.
- T17: False FAIL — an honest hand-off for unavailable Enterprise pricing was rejected.
- T19: False PASS — required notification troubleshooting steps were omitted.

## Limitations

The evaluation contains only 25 cases. These results do not establish
general performance across other datasets or tasks. The two examples
were selected from v1 disagreements. Human labels remain frozen and
must not be changed to improve judge agreement.

## Conclusion

Judge v2 improved measured agreement by 20 percentage points, but six
disagreements remain. Further improvements should address both false
PASS and false FAIL decisions and be evaluated against the same frozen
human labels.
