import os
import re
import json
from typing import Optional
from google import genai
from google.genai import types
from json_repair import repair_json

import numpy as np

CORNER_PROMPT = """This photo shows a paper invoice/document, possibly at an angle, possibly with other objects around it.
Identify the 4 corners of the PAPER DOCUMENT ONLY (not the background, not other objects).
Return ONLY JSON, no markdown fences:
{"top_left": {"x": 0, "y": 0}, "top_right": {"x": 0, "y": 0}, "bottom_right": {"x": 0, "y": 0}, "bottom_left": {"x": 0, "y": 0}}"""


def detect_document_corners(image_bytes: bytes) -> Optional[list]:
    """Ask Gemini to locate the invoice's 4 corners in a photo, as a starting
    point for the user to confirm/adjust -- never a hard dependency. Returns
    a list of 4 {"x", "y"} fractions (0.0-1.0) in tl/tr/br/bl order, or None
    if detection isn't available or fails for any reason."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'), CORNER_PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        text = response.text.strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        data = repair_json(text, return_objects=True)

        if not isinstance(data, dict):
            return None

        raw_points = [data.get(k) for k in ("top_left", "top_right", "bottom_right", "bottom_left")]
        if any(p is None or "x" not in p or "y" not in p for p in raw_points):
            return None

        # Gemini returns coordinates on its standard 0-1000 normalized scale
        # regardless of what the prompt asks for -- confirmed empirically.
        pts = np.array([[float(p["x"]) / 1000.0, float(p["y"]) / 1000.0] for p in raw_points], dtype="float32")

        if np.any(pts < -0.05) or np.any(pts > 1.05):
            return None

        # Guard against a degenerate/near-zero-area response.
        area = abs(
            pts[0][0] * (pts[1][1] - pts[3][1]) +
            pts[1][0] * (pts[2][1] - pts[0][1]) +
            pts[2][0] * (pts[3][1] - pts[1][1]) +
            pts[3][0] * (pts[0][1] - pts[2][1])
        ) / 2.0
        if area < 0.01:
            return None

        # Trust Gemini's own top_left/top_right/bottom_right/bottom_left
        # labeling as-is -- re-deriving order from geometric position (as
        # order_points() does) can disagree with the true reading orientation
        # for a document that's rotated far enough relative to the photo frame,
        # producing a sideways crop downstream.
        return [{"x": float(max(0.0, min(1.0, x))), "y": float(max(0.0, min(1.0, y)))} for x, y in pts]
    except Exception as e:
        print(f"[CORNER DETECTION] Failed, no starting corners suggested: {e}", flush=True)
        return None
