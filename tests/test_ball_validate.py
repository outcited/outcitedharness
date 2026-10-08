import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline.ball_validate import validate_ballmap


def rows(spec):
    out = []
    for designator, name in spec:
        out.append({"ball": designator, "name": name})
    return out


class BallValidateTests(unittest.TestCase):
    def test_valid_small_map_passes(self):
        spec = [("A1", "VDDIO"), ("A2", "GND"), ("B1", "GPIO1"), ("B2", "GPIO2"),
                ("C1", "GND"), ("C2", "VDD"), ("D1", "GPIO3"), ("D2", "GPIO4")]
        r = validate_ballmap(spec, package="0.8mm A4 (84-ball)")
        self.assertTrue(r["pass"])
        self.assertEqual(r["p0"], 0)

    def test_duplicate_ball_is_p0(self):
        spec = [("A1", "VDDIO"), ("A1", "GND"), ("B1", "GPIO"), ("B2", "GPIO2")]
        r = validate_ballmap(spec)
        self.assertFalse(r["pass"])
        self.assertTrue(any(i["check"] == "duplicate_ball" for i in r["issues"]))

    def test_invalid_designator_flagged(self):
        spec = [("I1", "VDD"), ("A2", "GND"), ("B1", "GPIO"), ("B2", "GPIO2")]
        r = validate_ballmap(spec)
        self.assertTrue(any(i["check"] == "invalid_letter" for i in r["issues"]))

    def test_garbage_designator_flagged(self):
        spec = [("F7", "VDD"), ("?", "GND"), ("B1", "GPIO"), ("B2", "GPIO2")]
        r = validate_ballmap(spec)
        self.assertTrue(any(i["check"] == "invalid_designator" for i in r["issues"]))

    def test_implausibly_few_balls(self):
        r = validate_ballmap(rows([("A1", "VDD"), ("A2", "GND")]))
        self.assertTrue(any(i["check"] == "implausibly_few_balls" for i in r["issues"]))

    def test_pin_cross_check_flags_unknown_ball_name(self):
        spec = [("A1", "VDDIO"), ("A2", "GND"), ("B1", "GPIO1"), ("B2", "MYSTERY_PIN")]
        pins = [{"name": "VDDIO"}, {"name": "GND"}, {"name": "GPIO1"}]
        r = validate_ballmap(spec, pin_rows=pins)
        self.assertTrue(any(i["check"] == "ball_not_in_pin_table" for i in r["issues"]))


if __name__ == "__main__":
    unittest.main()
