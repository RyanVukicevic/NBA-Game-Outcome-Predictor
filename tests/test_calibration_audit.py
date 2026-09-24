import unittest

import numpy as np

from experiments.calibration_audit import METHODS, apply_calibrator, fit_calibrators, score


class CalibrationAuditTests(unittest.TestCase):
    def setUp(self):
        self.probabilities = np.array([.08, .15, .22, .35, .45, .55, .65, .78, .85, .92] * 8)
        self.outcomes = np.array([0, 0, 0, 0, 1, 0, 1, 1, 1, 1] * 8)

    def test_all_calibrators_return_bounded_probabilities(self):
        fitted = fit_calibrators(self.probabilities, self.outcomes)
        for method in METHODS:
            result = apply_calibrator(method, fitted.get(method), self.probabilities)
            self.assertEqual(len(result), len(self.probabilities))
            self.assertTrue(np.all((result > 0) & (result < 1)))

    def test_raw_is_identity_and_scores_are_finite(self):
        result = apply_calibrator("raw", None, self.probabilities)
        np.testing.assert_allclose(result, self.probabilities)
        metrics = score(self.outcomes, result)
        self.assertEqual(metrics["games"], len(result))
        self.assertTrue(all(np.isfinite(value) for value in metrics.values()))

    def test_invalid_method_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_calibrator("unknown", None, self.probabilities)


if __name__ == "__main__":
    unittest.main()
