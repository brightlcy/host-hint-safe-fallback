import unittest

from simulator.run_lifetime_experiment import _parse_float_list, _parse_int_list, _profile_defaults


class RunLifetimeExperimentCLITests(unittest.TestCase):
    def test_quick_profile_is_smaller_than_core(self):
        quick = _profile_defaults("quick")
        core = _profile_defaults("core")
        self.assertLess(quick["total_writes"], core["total_writes"])
        self.assertLess(quick["logical_pages"], core["logical_pages"])
        self.assertEqual(quick["detector_window"], 128)

    def test_list_parsers(self):
        self.assertEqual(_parse_int_list("1, 2,3"), (1, 2, 3))
        self.assertEqual(_parse_float_list("0,.5,1"), (0.0, 0.5, 1.0))


if __name__ == "__main__":
    unittest.main()
