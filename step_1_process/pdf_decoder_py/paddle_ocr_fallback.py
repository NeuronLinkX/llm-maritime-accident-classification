#!/usr/bin/env python3
"""PaddleOCR-based local OCR fallback for scanned PDF pages.

Kept isolated from pdf_to_hwpx_then_decode.py so a missing/broken
PaddleOCR install never takes the main pipeline down with it: every
public entry point here catches its own exceptions and reports them
back as a string instead of raising.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class PaddleOCRUnavailable(RuntimeError):
    pass


_ENGINE_CACHE = {}

# lang -> (detection model, recognition model) pairing for the lightweight
# "mobile" model set. PaddleOCR's default for lang="korean" is
# PP-OCRv5_server_det, a much heavier CPU detector (~30s/page on a modern
# CPU vs ~8s/page for the mobile detector in our own benchmark, with
# near-identical box/text output). Set PADDLE_OCR_DET_MODEL=server to opt
# back into the heavier default if the extra accuracy is ever worth it.
_MOBILE_MODEL_PAIRS = {
    "korean": ("PP-OCRv5_mobile_det", "korean_PP-OCRv5_mobile_rec"),
}


def _run(cmd):
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {' '.join(cmd)}")


def render_pdf_pages(input_path, out_dir, dpi=240, max_pages=2):
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm (poppler-utils) is not installed")
    prefix = Path(out_dir) / "rendered_page"
    _run(["pdftoppm", "-png", "-r", str(dpi), "-f", "1", "-l", str(max_pages), str(input_path), str(prefix)])
    return sorted(Path(out_dir).glob("rendered_page-*.png"))[:max_pages]


def _load_engine(lang="korean"):
    if lang in _ENGINE_CACHE:
        return _ENGINE_CACHE[lang]
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise PaddleOCRUnavailable(f"paddleocr package is not installed ({exc})") from exc

    engine = None
    last_exc = None
    # PaddleOCR 3.x renamed use_angle_cls -> use_textline_orientation and
    # added doc preprocessing switches; older releases don't know those
    # kwargs at all. Try newest signature first, fall back to older ones.
    base_kwargs = {
        "use_textline_orientation": True,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
    }
    attempts = []
    mobile_pair = _MOBILE_MODEL_PAIRS.get(lang)
    if mobile_pair and os.environ.get("PADDLE_OCR_DET_MODEL", "mobile") != "server":
        det_model, rec_model = mobile_pair
        # NOTE: passing an explicit model name makes PaddleOCR ignore `lang`
        # entirely when picking the *other* model, so both must be given
        # together or recognition silently falls back to a non-Korean model.
        attempts.append({
            **base_kwargs,
            "text_detection_model_name": det_model,
            "text_recognition_model_name": rec_model,
        })
    attempts.append({"lang": lang, **base_kwargs})
    attempts.append({"lang": lang, "use_angle_cls": True, "show_log": False})
    attempts.append({"lang": lang})
    for kwargs in attempts:
        try:
            engine = PaddleOCR(**kwargs)
            break
        except TypeError:
            continue
        except Exception as exc:  # model download / backend init failure
            last_exc = exc
    if engine is None:
        raise PaddleOCRUnavailable(f"failed to initialize PaddleOCR ({last_exc})")
    _ENGINE_CACHE[lang] = engine
    return engine


def _box_center_height(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return sum(ys) / len(ys), min(xs), (max(ys) - min(ys))


def _page_items_from_new_api(page):
    items = []
    rec_texts = page.get("rec_texts") if hasattr(page, "get") else None
    if rec_texts is None:
        return items
    rec_polys = page.get("rec_polys")
    rec_boxes = page.get("rec_boxes")
    for i, text in enumerate(rec_texts):
        if not text or not str(text).strip():
            continue
        box = None
        if rec_polys is not None and i < len(rec_polys):
            box = rec_polys[i]
        elif rec_boxes is not None and i < len(rec_boxes) and len(rec_boxes[i]) == 4:
            x1, y1, x2, y2 = rec_boxes[i]
            box = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        if box is None:
            items.append((0.0, 0.0, 0.0, str(text)))
            continue
        cy, cx, h = _box_center_height(box)
        items.append((cy, cx, h, str(text)))
    return items


def _page_items_from_legacy_api(page_entries):
    items = []
    for entry in page_entries or []:
        try:
            box, rec = entry
            text = rec[0] if isinstance(rec, (list, tuple)) else str(rec)
        except (TypeError, ValueError, IndexError):
            continue
        if not text or not str(text).strip():
            continue
        cy, cx, h = _box_center_height(box)
        items.append((cy, cx, h, str(text)))
    return items


def _rows_with_y(items):
    """Cluster items into rows, returning (row_top_y, row_text) pairs."""
    if not items:
        return []
    heights = [h for (_, _, h, _) in items if h > 0]
    row_h = (sum(heights) / len(heights)) if heights else 20.0
    threshold = max(row_h * 0.6, 8.0)

    items = sorted(items, key=lambda t: t[0])
    rows = []
    current_row = []
    current_cy = None
    for cy, cx, h, text in items:
        if current_cy is None or abs(cy - current_cy) <= threshold:
            current_row.append((cx, cy, text))
            current_cy = cy if current_cy is None else current_cy
        else:
            rows.append(current_row)
            current_row = [(cx, cy, text)]
            current_cy = cy
    if current_row:
        rows.append(current_row)

    result = []
    for row in rows:
        row.sort(key=lambda t: t[0])
        row_y = min(cy for _, cy, _ in row)
        result.append((row_y, " ".join(text for _, _, text in row)))
    return result


def _rows_to_lines(items):
    return [text for _, text in _rows_with_y(items)]


def _split_columns(items):
    """Split a page's items into left/right columns if there's a clear gutter.

    Korean 재결요약서 documents commonly place a narrow label column
    ("사건명", "해양사고관련자", "판시사항", ...) to the left of a wide
    body column. Naive top-to-bottom/left-to-right ordering interleaves
    unrelated label and body text on the same visual row, which corrupts
    downstream keyword extraction. When a wide enough horizontal gutter
    exists, the two columns are extracted separately so the caller can
    merge them label-by-label instead of row-by-row across the whole page
    (see _merge_label_body_columns).
    """
    if len(items) < 6:
        return [items]
    xs = sorted(it[1] for it in items)
    span = xs[-1] - xs[0]
    if span <= 0:
        return [items]

    lo = max(1, int(len(xs) * 0.10))
    hi = min(len(xs) - 1, int(len(xs) * 0.65))
    best_gap = 0.0
    best_at = None
    for i in range(lo, hi):
        gap = xs[i] - xs[i - 1]
        if gap > best_gap:
            best_gap = gap
            best_at = xs[i - 1] + gap / 2.0

    if best_at is None or best_gap < span * 0.06:
        return [items]

    left = [it for it in items if it[1] < best_at]
    right = [it for it in items if it[1] >= best_at]
    if len(left) < 2 or len(right) < 2:
        return [items]
    return [left, right]


def _merge_label_body_columns(left_items, right_items):
    """Interleave a narrow label column with a wide body column.

    Reading "all of the label column, then all of the body column" (the
    previous behavior) separates a label from its own multi-line content,
    and worse: a body row that has no label of its own (e.g. a sentence
    that wraps across a page boundary and continues at the top of the next
    page, before the next real label like "주문") ends up placed *after*
    unrelated later labels instead of before them. Both corrupt reading
    order for downstream keyword extraction.

    Instead, treat the (sparse) label rows as anchors in y-order and walk
    the (dense) body rows in y-order, emitting a label as soon as the next
    body row's y has reached it. A body row with no preceding label just
    gets appended as-is (e.g. a continuation row above the first label).
    """
    left_rows = _rows_with_y(left_items)
    right_rows = _rows_with_y(right_items)
    if not left_rows:
        return [text for _, text in right_rows]
    if not right_rows:
        return [text for _, text in left_rows]

    lines = []
    li = 0
    for y, text in right_rows:
        while li < len(left_rows) and left_rows[li][0] <= y:
            lines.append(left_rows[li][1])
            li += 1
        lines.append(text)
    while li < len(left_rows):
        lines.append(left_rows[li][1])
        li += 1
    return lines


def _items_to_reading_order_lines(items):
    if not items:
        return []
    columns = _split_columns(items)
    if len(columns) == 1:
        return _rows_to_lines(columns[0])
    left, right = columns
    return _merge_label_body_columns(left, right)


def _ocr_single_image(engine, image_path):
    """Run OCR on one page image and return reading-order text lines."""
    if hasattr(engine, "predict"):
        result = list(engine.predict(str(image_path)))
        items = []
        for page in result:
            items.extend(_page_items_from_new_api(page))
        return _items_to_reading_order_lines(items)

    # Legacy .ocr() API: result is [[box, (text, score)], ...] for a single
    # image, or [[[box, (text, score)], ...]] when nested by page.
    try:
        result = engine.ocr(str(image_path), cls=True)
    except TypeError:
        result = engine.ocr(str(image_path))
    if result and isinstance(result[0], list) and result and result[0] and isinstance(result[0][0], list) \
            and len(result[0][0]) == 2 and isinstance(result[0][0][0], list):
        # nested-by-page shape: unwrap the single page
        page_entries = result[0]
    else:
        page_entries = result
    return _items_to_reading_order_lines(_page_items_from_legacy_api(page_entries))


def _split_page_image_left_right(image_path, out_dir):
    """Split one rendered page image into left/right halves.

    Mirrors the native-text path's landscape "two pages scanned side by
    side onto one physical page" handling (see extract_pdf_native_text's
    pdftotext -x/-W crop). Without this, a single OCR pass over the whole
    image interleaves the left page's label column with the right page's
    body column (and vice versa), corrupting reading order and silently
    truncating long fields like 사고 경위 right where the pages meet.
    """
    from PIL import Image

    img = Image.open(image_path)
    width, height = img.size
    mid = width // 2
    stem = Path(image_path).stem
    left_path = Path(out_dir) / f"{stem}_L.png"
    right_path = Path(out_dir) / f"{stem}_R.png"
    img.crop((0, 0, mid, height)).save(left_path)
    img.crop((mid, 0, width, height)).save(right_path)
    return [left_path, right_path]


def ocr_pdf(input_path, dpi=240, max_pages=2, lang="korean", render_dir=None, two_up=False):
    """Run PaddleOCR over the first `max_pages` pages of input_path.

    `two_up=True` should be passed when the PDF's pages are physically two
    document pages scanned side by side (pdf_layout_type ==
    "landscape_two_up"); each rendered page image is then split into left
    and right halves and OCR'd/read in that order, matching how the native
    text extractor already treats these PDFs.

    Returns (text, lines, error). On any failure text/lines are empty and
    error carries a human-readable message; this function never raises.
    """
    try:
        engine = _load_engine(lang)
    except Exception as exc:
        return "", [], str(exc)

    def _run_ocr(render_to):
        images = render_pdf_pages(input_path, render_to, dpi=dpi, max_pages=max_pages)
        if not images:
            raise RuntimeError("failed to render PDF pages for PaddleOCR")
        if two_up:
            split_images = []
            for image in images:
                split_images.extend(_split_page_image_left_right(image, render_to))
            images = split_images
        page_texts = []
        for image in images:
            page_lines = _ocr_single_image(engine, image)
            if page_lines:
                page_texts.append("\n".join(page_lines))
        return page_texts

    try:
        if render_dir:
            Path(render_dir).mkdir(parents=True, exist_ok=True)
            page_texts = _run_ocr(render_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="pdf_paddleocr_") as td:
                page_texts = _run_ocr(td)
    except Exception as exc:
        return "", [], f"PaddleOCR run failed: {exc}"

    text = "\n\f\n".join(page_texts)
    lines = [line for chunk in page_texts for line in chunk.splitlines() if line.strip()]
    return text, lines, ""
