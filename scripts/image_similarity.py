# -*- coding: utf-8 -*-
"""Near-duplicate image detection inside one paper (requires Pillow).

sha1 only finds byte-identical files. Reused panels are usually re-saved, resized, cropped
into a multi-panel figure, rotated or mirrored. This module:

1. splits every figure into panels along blank gutters (rows, then columns);
2. keeps photo-like panels only (blots, gels, micrographs, photos): line and bar charts
   share layouts and would match each other, so low-entropy panels are skipped;
3. computes a 256-bit difference hash for each panel and compares every pair of panels
   (also within the same figure) under the 8 rotations/mirrorings;
4. writes a side-by-side image for each candidate pair so the model can look at it.

A hash match is a lead for visual inspection, not proof of duplication.
"""

import math
import os

HASH_SIZE = 16          # 16x16 = 256-bit dHash
MAX_DISTANCE = 30       # <= ~12% differing bits
MIN_PANEL = 64          # px, smaller pieces are labels or icons
MIN_ENTROPY = 4.5       # bits; photo-like content
GUTTER_LEVEL = 235      # grey level that counts as blank
MAX_PANELS_PER_IMAGE = 48
MAX_PAIRS = 30

TRANSFORMS = ["identity", "rot90", "rot180", "rot270", "flipH", "flipV", "transpose", "transverse"]


def _transform(img, name):
    from PIL import Image

    ops = {
        "identity": None,
        "rot90": Image.Transpose.ROTATE_90,
        "rot180": Image.Transpose.ROTATE_180,
        "rot270": Image.Transpose.ROTATE_270,
        "flipH": Image.Transpose.FLIP_LEFT_RIGHT,
        "flipV": Image.Transpose.FLIP_TOP_BOTTOM,
        "transpose": Image.Transpose.TRANSPOSE,
        "transverse": Image.Transpose.TRANSVERSE,
    }
    op = ops[name]
    return img if op is None else img.transpose(op)


def dhash_bits(gray):
    from PIL import Image

    small = gray.resize((HASH_SIZE + 1, HASH_SIZE), Image.Resampling.LANCZOS)
    px = small.tobytes()
    width = HASH_SIZE + 1
    value = 0
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            value = (value << 1) | (1 if px[row * width + col] > px[row * width + col + 1] else 0)
    return value


def entropy(gray):
    hist = gray.histogram()
    total = float(sum(hist)) or 1.0
    return -sum((h / total) * math.log2(h / total) for h in hist if h)


def _segments(profile, min_len):
    """Runs of non-blank positions in a 0/1 profile, merged across tiny gaps."""
    runs, start = [], None
    for i, busy in enumerate(profile + [0]):
        if busy and start is None:
            start = i
        elif not busy and start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
    return runs


def split_panels(gray):
    """Boxes (left, top, right, bottom) of panels separated by blank gutters."""
    w, h = gray.size
    px = gray.load()
    step = max(1, min(w, h) // 400)

    def row_busy(y, x0, x1):
        dark = sum(1 for x in range(x0, x1, step) if px[x, y] < GUTTER_LEVEL)
        return dark > 0.02 * ((x1 - x0) / step)

    def col_busy(x, y0, y1):
        dark = sum(1 for y in range(y0, y1, step) if px[x, y] < GUTTER_LEVEL)
        return dark > 0.02 * ((y1 - y0) / step)

    boxes = []
    for top, bottom in _segments([1 if row_busy(y, 0, w) else 0 for y in range(h)], MIN_PANEL):
        for left, right in _segments([1 if col_busy(x, top, bottom) else 0 for x in range(w)], MIN_PANEL):
            boxes.append((left, top, right, bottom))
    return boxes[:MAX_PANELS_PER_IMAGE]


def collect_panels(image_paths):
    """[(file, box, gray_panel)] for photo-like panels, plus the whole image when it is photo-like."""
    from PIL import Image

    panels = []
    for path in image_paths:
        try:
            with Image.open(path) as im:
                im.load()
                gray = im.convert("L")
        except Exception:
            continue
        w, h = gray.size
        if w < MIN_PANEL or h < MIN_PANEL:
            continue
        boxes = split_panels(gray)
        if (0, 0, w, h) not in boxes and len(boxes) != 1:
            boxes = [(0, 0, w, h)] + boxes
        for box in boxes:
            crop = gray.crop(box)
            if entropy(crop) < MIN_ENTROPY:
                continue
            panels.append((path, box, crop))
    return panels


def _contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def find_near_duplicates(image_paths, out_dir=None, max_distance=MAX_DISTANCE):
    """Candidate pairs of near-identical panels, best matches first."""
    panels = collect_panels(image_paths)
    hashes = []
    for path, box, crop in panels:
        hashes.append({name: dhash_bits(_transform(crop, name)) for name in TRANSFORMS})
    pairs = []
    for i in range(len(panels)):
        for j in range(i + 1, len(panels)):
            a_path, a_box, _ = panels[i]
            b_path, b_box, _ = panels[j]
            if a_path == b_path and (_contains(a_box, b_box) or _contains(b_box, a_box)):
                continue  # a panel and the figure that contains it
            base = hashes[i]["identity"]
            best = min(((bin(base ^ hashes[j][name]).count("1"), name) for name in TRANSFORMS))
            if best[0] <= max_distance:
                pairs.append({
                    "a": {"file": os.path.basename(a_path), "box": list(a_box)},
                    "b": {"file": os.path.basename(b_path), "box": list(b_box)},
                    "hammingDistance": best[0],
                    "transform": best[1],
                    "sameFigure": a_path == b_path,
                    "_i": i, "_j": j,
                })
    pairs.sort(key=lambda p: p["hammingDistance"])
    # one report per panel pair region: drop pairs whose panels were already matched
    kept, used = [], set()
    for pair in pairs:
        key = (pair["_i"], pair["_j"])
        if pair["_i"] in used and pair["_j"] in used:
            continue
        used.update(key)
        kept.append(pair)
        if len(kept) >= MAX_PAIRS:
            break
    if out_dir:
        for index, pair in enumerate(kept, 1):
            pair["preview"] = _write_pair_preview(panels[pair["_i"]][2], panels[pair["_j"]][2], pair["transform"], out_dir, index)
    for pair in kept:
        pair.pop("_i", None)
        pair.pop("_j", None)
    return kept


def _write_pair_preview(a, b, transform, out_dir, index, height=320):
    from PIL import Image

    b_aligned = _transform(b, transform) if transform != "identity" else b
    # the hash compared A with transform(B); show B undone so both look alike
    tiles = []
    for img in (a, b_aligned):
        scale = height / img.size[1]
        tiles.append(img.resize((max(1, int(img.size[0] * scale)), height)))
    sheet = Image.new("L", (tiles[0].size[0] + tiles[1].size[0] + 16, height), 255)
    sheet.paste(tiles[0], (0, 0))
    sheet.paste(tiles[1], (tiles[0].size[0] + 16, 0))
    name = f"_near_dup_{index:02d}.png"
    sheet.save(os.path.join(out_dir, name))
    return name
