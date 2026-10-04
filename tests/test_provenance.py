from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from provenance import MODEL_SOURCE_FILES, implementation_id


class ProvenanceTests(unittest.TestCase):
    def test_model_identity_has_an_explicit_prediction_scope(self):
        self.assertEqual(len(implementation_id()), 64)
        self.assertIn("modeling.py", MODEL_SOURCE_FILES)
        self.assertIn("features.py", MODEL_SOURCE_FILES)
        self.assertNotIn("tracking.py", MODEL_SOURCE_FILES)
        self.assertNotIn("scheduler.py", MODEL_SOURCE_FILES)
        self.assertNotIn("track.py", MODEL_SOURCE_FILES)


if __name__ == "__main__":
    unittest.main()
