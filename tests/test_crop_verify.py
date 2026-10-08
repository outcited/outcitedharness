import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline.crop_verify import ocr_crop, verify_cell

PDF = "/Volumes/M5_4TB/exports/power-datasheet-pairs/ti-20260908/pdf/ti-2N7002L-Q1.pdf"


class CropVerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pymupdf
        doc = pymupdf.open(PDF)
        page = doc[3]
        cls.tables = list(page.find_tables().tables)
        cls.page_rect = page.rect
        doc.close()

    def test_ocr_reads_real_table_region(self):
        t = self.tables[0]
        text = ocr_crop(PDF, 4, t.bbox)
        self.assertGreater(len(text.strip()), 10)

    def test_correct_value_supported(self):
        t = self.tables[0]
        ocr = ocr_crop(PDF, 4, t.bbox)
        import re
        nums = re.findall(r"\d+(?:\.\d+)?", ocr.replace(",", ""))
        self.assertTrue(nums, "OCR produced no numbers to test with")
        v = verify_cell(PDF, 4, t.bbox, nums[0])
        self.assertEqual(v["verdict"], "SUPPORTED")
        self.assertEqual(v["severity"], "P2")

    def test_wrong_value_caught(self):
        t = self.tables[0]
        ocr = ocr_crop(PDF, 4, t.bbox)
        import re
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", ocr.replace(",", ""))]
        self.assertTrue(nums)
        wrong = max(nums) + 98765.5
        v = verify_cell(PDF, 4, t.bbox, wrong)
        self.assertEqual(v["verdict"], "UNSUPPORTED")
        self.assertEqual(v["reason_code"], "cell_ocr_mismatch")
        self.assertEqual(v["severity"], "P0")

    def test_empty_bbox_review(self):
        v = verify_cell(PDF, 4, (10, 10, 12, 12), "5.5")
        self.assertEqual(v["severity"], "P1")


if __name__ == "__main__":
    unittest.main()
