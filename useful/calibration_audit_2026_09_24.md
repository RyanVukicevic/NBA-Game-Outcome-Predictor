# Probability Calibration Audit - September 24, 2026

Production currently uses the raw probability from the balanced logistic
regression. It does not apply a separate post-hoc calibrator.

The audit compared raw probabilities with sigmoid/Platt, temperature, beta and
isotonic calibration on the same 3,884 outer-season predictions. For each outer
season, calibrators were fitted only on earlier chronological inner predictions.
No evaluated outer-season outcome entered its calibrator.

| Method | Accuracy | Log loss | Brier | ECE10 |
| --- | ---: | ---: | ---: | ---: |
| Beta | 66.14% | 0.612978 | 0.211989 | 0.029599 |
| Sigmoid | 66.53% | 0.613311 | 0.212040 | 0.029834 |
| Raw production recipe | 65.81% | 0.615281 | 0.212901 | 0.046302 |
| Temperature | 65.81% | 0.617088 | 0.213759 | 0.049728 |
| Isotonic | 66.19% | 0.626064 | 0.212103 | 0.029234 |

Beta calibration had the best pooled historical log loss. Compared with raw, its
week-block bootstrap change was -0.00224 log loss (95% interval -0.00656 to
+0.00208) and -0.000884 Brier (95% interval -0.00293 to +0.00111). Both intervals
cross zero. Beta and sigmoid also worsened log loss in the first outer season and
improved it in the following two.

Therefore beta is a prospective challenger, not a proven upgrade. The strict gate
requires pooled Brier and log-loss improvement, improvement in at least two outer
seasons, and week-block confidence intervals entirely below zero. It did not pass.
Production was not changed. Repeated inspection means these historical seasons are
development evidence; the next decision must use frozen future predictions.

Reproduce with:

```powershell
.venv\Scripts\python.exe -B experiments\calibration_audit.py `
  --output reports\experiments\NEW-calibration-audit
```

Detailed artifacts include predictions, season metrics, reliability bins, fitted
method audit records, paired-week uncertainty and a manifest.
