import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from harness.pipeline.region_detect import pad_bbox, regions_for_page, render_regions

PDF = "/Volumes/M5_4TB/exports/power-datasheet-pairs/ti-20260908/pdf/ti-2N7002L-Q1.pdf"


class RegionDetectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(PDF)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_text_native_page_finds_table_bboxes(self):
        page = self.doc[3]
        regions = regions_for_page(page, page.get_text("text"))
        self.assertTrue(regions)
        self.assertTrue(all(r["source"] == "tables" for r in regions))
        b = regions[0]["bbox"]
        self.assertTrue(b[2] > b[0] and b[3] > b[1])

    def test_pad_clamps_to_page(self):
        rect = pymupdf.Rect(0, 0, 500, 700)
        b = pad_bbox((-50, -50, 550, 750), rect)
        self.assertEqual(b, (0, 0, 500, 700))

    def test_sparse_page_gets_fullpage(self):
        page = self.doc[0]
        regions = regions_for_page(page, "")
        self.assertEqual(regions[0]["source"], "fullpage")

    def test_render_regions_produces_pngs(self):
        out = render_regions(PDF, [4], dpi=150)
        self.assertIn(4, out)
        self.assertTrue(out[4])
        bbox, png = out[4][0]
        self.assertGreater(len(png), 5000)


if __name__ == "__main__":
    unittest.main()
