"""Image processing and vision AI for desktop interaction.

Provides crop, normalize, grid tools, and Gemini Flash coordinate finding.

Lab results (42 tests): small crops achieve 19px accuracy vs 268px on full screenshots.
JSON structural data outperforms image-only (50% vs 0% for Sonnet, 40% vs 20% for Flash).
"""

import base64
import io
import logging
import os
import re
import time
from typing import Optional

import requests

log = logging.getLogger("screenbox.vision")

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = os.environ.get("SCREENBOX_VISION_MODEL", "google/gemini-2.0-flash-001")


def crop_screenshot(img_bytes: bytes, region: tuple[int, int, int, int],
                    quality: int = 85) -> tuple[bytes, dict]:
    """Crop screenshot to region.

    Args:
        img_bytes: Full screenshot JPEG bytes
        region: (x1, y1, x2, y2) pixel coordinates
        quality: JPEG quality for output

    Returns:
        (cropped_jpeg_bytes, metadata_dict)
        metadata includes: x1, y1, x2, y2, crop_w, crop_h, full_w, full_h
    """
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    full_w, full_h = img.size

    x1, y1, x2, y2 = region
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(full_w, x2), min(full_h, y2)

    crop = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=quality)

    meta = {
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "crop_w": crop.size[0], "crop_h": crop.size[1],
        "full_w": full_w, "full_h": full_h,
    }
    return buf.getvalue(), meta


def normalize_screenshot(img_bytes: bytes, width: int = 1000,
                         pad_to_square: bool = True,
                         quality: int = 85) -> tuple[bytes, dict]:
    """Resize screenshot to fixed width, optionally pad to square.

    Args:
        img_bytes: Screenshot JPEG bytes
        width: Target width in pixels
        pad_to_square: If True, pad height with black to make square
        quality: JPEG quality

    Returns:
        (normalized_jpeg_bytes, metadata_dict)
        metadata includes: original_w, original_h, scale, padded
    """
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    orig_w, orig_h = img.size

    scale = width / orig_w
    new_h = int(orig_h * scale)
    img = img.resize((width, new_h), Image.LANCZOS)

    padded = False
    if pad_to_square and new_h < width:
        canvas = Image.new("RGB", (width, width), (0, 0, 0))
        canvas.paste(img, (0, 0))
        img = canvas
        padded = True

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)

    meta = {
        "original_w": orig_w, "original_h": orig_h,
        "scale": round(scale, 4),
        "output_w": img.size[0], "output_h": img.size[1],
        "padded": padded,
    }
    return buf.getvalue(), meta


def grid_overlay(img_bytes: bytes, cols: int = 3, rows: int = 3,
                 quality: int = 85) -> tuple[bytes, dict]:
    """Draw numbered grid overlay on screenshot.

    Each cell gets a visible number label. Returns image + cell coordinate map.

    Args:
        img_bytes: Screenshot JPEG bytes
        cols: Grid columns
        rows: Grid rows
        quality: JPEG quality

    Returns:
        (image_with_grid_bytes, cell_map)
        cell_map: {"1": {"x1":0,"y1":0,"x2":426,"y2":240,"cx":213,"cy":120}, ...}
    """
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    cell_w = w // cols
    cell_h = h // rows
    cells = {}

    # Try to load a font, fall back to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c + 1
            x1 = c * cell_w
            y1 = r * cell_h
            x2 = x1 + cell_w
            y2 = y1 + cell_h
            cx = x1 + cell_w // 2
            cy = y1 + cell_h // 2

            cells[str(idx)] = {
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "cx": cx, "cy": cy,
            }

            # Draw grid lines
            draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=2)

            # Draw cell number with background
            label = str(idx)
            bbox = font.getbbox(label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            lx, ly = x1 + 8, y1 + 8
            draw.rectangle([lx - 2, ly - 2, lx + tw + 4, ly + th + 4], fill=(0, 0, 0, 180))
            draw.text((lx, ly), label, fill=(0, 255, 0), font=font)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), cells


def enhance_screenshot(img_bytes: bytes, quality: int = 85) -> tuple[bytes, dict]:
    """Enhance screenshot for better visibility of dark-on-dark and light-on-light elements.

    Applies: auto-invert dark regions, unsharp masking, adaptive contrast.
    Makes window control buttons, faint borders, and low-contrast UI visible.

    Based on lab findings (v5-pipeline/rect-debug.py):
    - Auto-invert: dark themes become light, buttons pop out
    - Unsharp mask: edges and thin lines get amplified
    - CLAHE: local contrast boost without blowing out bright areas

    Args:
        img_bytes: Screenshot JPEG bytes

    Returns:
        (enhanced_jpeg_bytes, metadata_dict)
    """
    import numpy as np
    from PIL import Image, ImageFilter

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    orig_w, orig_h = img.size
    arr = np.array(img)

    # 1. Convert to grayscale for analysis
    gray = np.mean(arr, axis=2)
    median_brightness = np.median(gray)

    # 2. Auto-invert if dark theme (median < 128)
    inverted = False
    if median_brightness < 128:
        arr = 255 - arr
        inverted = True

    # 3. CLAHE -- adaptive local contrast (works per-channel)
    try:
        import cv2
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        arr = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    except ImportError:
        # Fallback: simple contrast stretch per channel
        for ch in range(3):
            c = arr[:, :, ch].astype(np.float32)
            lo, hi = np.percentile(c, 2), np.percentile(c, 98)
            if hi - lo > 10:
                c = np.clip((c - lo) / (hi - lo) * 255, 0, 255)
                arr[:, :, ch] = c.astype(np.uint8)

    # 4. Unsharp masking -- boost edges
    img = Image.fromarray(arr)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)

    meta = {
        "original_w": orig_w, "original_h": orig_h,
        "median_brightness": round(float(median_brightness), 1),
        "inverted": inverted,
        "enhanced": True,
    }
    return buf.getvalue(), meta


def find_element(img_bytes: bytes, question: str,
                 region: Optional[tuple[int, int, int, int]] = None,
                 context_json: Optional[str] = None,
                 model: str = "flash") -> dict:
    """Find UI element coordinates using Gemini Flash vision.

    Sends a cropped region (or full screenshot) to Gemini Flash via OpenRouter.
    The model returns pixel coordinates of the requested element.

    Lab results: Gemini Flash achieves 40% accuracy with JSON context,
    20% image-only. Combined with crop (not full screenshot), accuracy is much higher.

    Args:
        img_bytes: Screenshot JPEG bytes
        question: What to find (e.g. "minimize button", "Close button", "search input")
        region: Optional crop region (x1, y1, x2, y2). If given, crops first.
        context_json: Optional JSON structural data (window positions, element list)
        model: Model to use: "flash" (default), "sonnet", "haiku"

    Returns:
        dict with: found, x, y (screen coords), local_x, local_y (crop coords),
                   region, model, raw_answer
    Requires OPENROUTER_API_KEY env var. Returns error if not set.
    """
    if not OPENROUTER_KEY:
        return {"found": False, "error": "OPENROUTER_API_KEY not set. Gemini vision disabled."}

    from PIL import Image

    models = {
        "flash": "google/gemini-2.0-flash-001",
        "sonnet": "anthropic/claude-sonnet-4",
        "haiku": "anthropic/claude-haiku-4-5",
    }

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    full_w, full_h = img.size

    # Crop if region specified
    offset_x, offset_y = 0, 0
    if region:
        x1, y1, x2, y2 = region
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(full_w, x2), min(full_h, y2)
        img = img.crop((x1, y1, x2, y2))
        offset_x, offset_y = x1, y1

    crop_w, crop_h = img.size

    # Encode image
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    # Build system prompt
    system = (
        f"You are looking at a {crop_w}x{crop_h} pixel region of a desktop screen.\n"
        "Find the requested UI element and return its coordinates within this image.\n"
        "Reply with ONLY: x,y (coordinates of the element center, in pixels from top-left of this image).\n"
        "If the element is not visible, reply: NOT_FOUND"
    )

    # Build user content
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
    ]

    user_text = f"Find: {question}"
    if context_json:
        user_text += f"\n\nStructural context:\n{context_json}"

    content.append({"type": "text", "text": user_text})

    # Call OpenRouter
    t0 = time.time()
    try:
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": models.get(model, models["flash"]),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                "max_tokens": 50,
                "temperature": 0,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error("OpenRouter call failed: %s", e)
        return {"found": False, "error": str(e), "model": model}

    elapsed_ms = int((time.time() - t0) * 1000)
    answer = data["choices"][0]["message"]["content"].strip()
    log.info("find_element [%s] %dms: %r -> %r", model, elapsed_ms, question, answer)

    # Parse coordinates
    m = re.search(r"(\d{1,4})\s*,\s*(\d{1,4})", answer)
    if m:
        local_x, local_y = int(m.group(1)), int(m.group(2))
        return {
            "found": True,
            "x": local_x + offset_x,
            "y": local_y + offset_y,
            "local_x": local_x,
            "local_y": local_y,
            "region": {"x1": offset_x, "y1": offset_y,
                       "x2": offset_x + crop_w, "y2": offset_y + crop_h},
            "crop_size": f"{crop_w}x{crop_h}",
            "model": model,
            "elapsed_ms": elapsed_ms,
            "raw_answer": answer,
        }

    return {
        "found": False,
        "raw_answer": answer,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "crop_size": f"{crop_w}x{crop_h}",
    }
