def verify_with_source_geometry(pdf_path: str, page_1based: int, region_bbox: list,
                                 table_columns: list, row_cells: dict,
                                 target_col: str, expected_value: str) -> dict:
    """Verify a cell using source text geometry (pymupdf word bboxes).

    Instead of cropping the entire row and matching anywhere:
    1. Find the target word's exact bbox from pymupdf source text
    2. Crop ONLY that word's cell region
    3. OCR the cell crop
    4. Match expected value against the cell OCR

    Returns {verdict, reason, ocr_text, cell_bbox, ...}
    """
    if not pdf_path:
        return {"verdict": "REVIEW", "reason": "no_source_pdf"}

    try:
        import pymupdf
        import tempfile
        import subprocess

        doc = pymupdf.open(pdf_path)
        if page_1based > len(doc):
            doc.close()
            return {"verdict": "REVIEW", "reason": "page_out_of_range"}

        page = doc[page_1based - 1]

        # Get all words with bboxes
        words = page.get_text("words")

        # Find the expected value's word bbox in the source
        target_word_bbox = None
        exp_str = str(expected_value).strip()

        for w in words:
            wx0, wy0, wx1, wy1, wtext = w[0], w[1], w[2], w[3], w[4]
            if wtext.strip() == exp_str:
                # Check if it's in the right region
                if (region_bbox[0] <= wx0 <= region_bbox[2] and
                        region_bbox[1] <= wy0 <= region_bbox[3]):
                    target_word_bbox = (wx0, wy0, wx1, wy1)
                    break

        if not target_word_bbox:
            # Value not found as a source word — try numeric core
            numeric_core = _extract_numeric_core(exp_str)
            if numeric_core:
                for w in words:
                    wx0, wy0, wx1, wy1, wtext = w[0], w[1], w[2], w[3], w[4]
                    if wtext.strip() == numeric_core:
                        if (region_bbox[0] <= wx0 <= region_bbox[2] and
                                region_bbox[1] <= wy0 <= region_bbox[3]):
                            target_word_bbox = (wx0, wy0, wx1, wy1)
                            break

        if not target_word_bbox:
            doc.close()
            return {"verdict": "REVIEW", "reason": "value_not_in_source_geometry",
                    "expected": exp_str}

        # Crop the cell with padding (2pt on each side)
        PAD = 2.0
        cell_bbox = (
            max(0, target_word_bbox[0] - PAD),
            max(0, target_word_bbox[1] - PAD),
            target_word_bbox[2] + PAD,
            target_word_bbox[3] + PAD,
        )

        # OCR the cell crop
        clip = pymupdf.Rect(*cell_bbox)
        pix = page.get_pixmap(dpi=300, clip=clip)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            pix.save(f.name)
            result = subprocess.run(
                ["tesseract", f.name, "stdout", "--psm", "7"],
                capture_output=True, text=True, timeout=30
            )
            ocr_text = result.stdout.strip()

        # Cleanup temp file
        import os
        try:
            os.unlink(f.name)
        except OSError:
            pass

        doc.close()

        if not ocr_text:
            return {"verdict": "REVIEW", "reason": "empty_cell_ocr",
                    "cell_bbox": cell_bbox, "ocr_text": ""}

        # Match expected against cell OCR
        if exp_str in ocr_text:
            return {"verdict": "SUPPORTED", "reason": "cell_exact_match",
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": target_word_bbox}

        if exp_str.upper() in ocr_text.upper():
            return {"verdict": "SUPPORTED", "reason": "cell_case_insensitive",
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": target_word_bbox}

        numeric_core = _extract_numeric_core(exp_str)
        if numeric_core and numeric_core in ocr_text:
            return {"verdict": "SUPPORTED", "reason": "cell_numeric_match",
                    "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                    "word_bbox": target_word_bbox}

        return {"verdict": "REVIEW", "reason": "cell_no_match",
                "ocr_text": ocr_text, "cell_bbox": cell_bbox,
                "expected": exp_str}

    except Exception as e:
        return {"verdict": "REVIEW", "reason": f"verify_error:{str(e)[:60]}"}
