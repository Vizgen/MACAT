"""Coordinate and binning tests for the all-gene alignment background."""
import unittest
from unittest.mock import patch

import numpy as np

from web import transcript_density


class TranscriptDensityTests(unittest.TestCase):
    def test_prefers_parquet_without_loading_table(self):
        with patch("web.transcript_density.os.path.exists") as exists:
            exists.side_effect = lambda p: p.endswith(".parquet")
            self.assertTrue(
                transcript_density.find_table("/experiment").endswith(
                    "detected_transcripts.parquet"))

    def test_inverse_registration_affine_bins_on_exact_plane_grid(self):
        # Registration stored: original_xy = oriented_xy @ linear + offset.
        linear = np.array([[0.0, 2.0], [-3.0, 0.0]])
        offset = np.array([100.0, 200.0])
        morig = np.vstack((linear, offset))
        oriented = np.array([
            [10.0, 20.0],  # row 0, col 0
            [14.0, 22.0],  # row 1, col 2 (twice)
            [14.0, 22.0],
            [30.0, 40.0],  # outside
            [np.nan, 20.0],
        ])
        original = oriented @ linear + offset

        def batches(_path):
            yield original[:2, 0], original[:2, 1]
            yield original[2:, 0], original[2:, 1]

        bundle = {
            "Jimg": np.zeros((4, 5)),
            "Morig": morig,
            "r0": 20.0,
            "c0": 10.0,
            "drow": 2.0,
            "dcol": 2.0,
        }
        got = transcript_density.on_plane(bundle, "unused", batches=batches)
        expected = np.zeros((4, 5))
        expected[0, 0] = 1
        expected[1, 2] = 2
        np.testing.assert_array_equal(got, expected)


if __name__ == "__main__":
    unittest.main()
