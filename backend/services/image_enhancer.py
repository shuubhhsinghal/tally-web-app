import cv2
import numpy as np
import pytesseract
import re

from backend.services.image_processing.image_preprocessor import warp_ordered_quad

# ==========================================
# EXPERIMENTAL PARAMETERS
# ==========================================
CONFIG = {
    # Orientation
    "osd_confidence_threshold": 1.0,

    # Deskew (Hough-line based -- see deskew() docstring for why)
    "deskew_angle_limit": 10.0,
    "deskew_min_angle": 0.3,
    "deskew_min_lines": 10,
    "deskew_max_angle_std": 1.5,

    # Perspective
    "perspective_min_area": 0.5,

    # Illumination Correction
    "illum_kernel_size": (101, 101), # Large kernel for background estimation
    "illum_strength": 0.8,           # How strongly to apply illumination correction

    # Upscale
    "upscale_min_dimension": 2000,
    "upscale_factor": 1.5,
}
# ==========================================

def auto_orient(image):
    try:
        h, w = image.shape[:2]
        ratio = 1500.0 / max(h, w)
        if ratio < 1.0:
            small = cv2.resize(image, (int(w * ratio), int(h * ratio)))
        else:
            small = image
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        osd = pytesseract.image_to_osd(gray, config='--psm 0')
        rot_match = re.search(r'Rotate: (\d+)', osd)
        conf_match = re.search(r'Orientation confidence: ([\d\.]+)', osd)

        if rot_match and conf_match:
            rot = int(rot_match.group(1))
            conf = float(conf_match.group(1))
            if conf > CONFIG["osd_confidence_threshold"]:
                if rot == 90:
                    image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                elif rot == 180:
                    image = cv2.rotate(image, cv2.ROTATE_180)
                elif rot == 270:
                    image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
                return image
    except Exception:
        pass
    return image

def deskew(image):
    """Detect and correct small residual skew using the document's own long,
    near-horizontal lines (table rulings, borders) via a Hough transform.

    A generic Otsu-threshold text mask (the original approach here) reliably
    fails on real dense invoices: tables, stamps, QR codes and handwriting
    push 30-47% of the frame to "foreground", swamping the actual skew signal
    and always returning a degenerate result. Verified: on a real invoice
    with a genuine ~3 degree residual tilt (left uncorrected by the initial
    corner-crop), that approach detected nothing, while this one detects a
    tight, consistent angle from the table's own ruling lines and correcting
    it fixed a real row-to-row value misalignment in extraction.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = image.shape[:2]
    edges = cv2.Canny(gray, 30, 100, apertureSize=3)

    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=150,
                             minLineLength=int(w * 0.25), maxLineGap=15)
    if lines is None:
        return image

    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        angle = np.degrees(np.arctan2(int(y2) - int(y1), int(x2) - int(x1)))
        if abs(angle) < 15:  # keep near-horizontal candidates only
            angles.append(angle)

    if len(angles) < CONFIG["deskew_min_lines"]:
        return image

    angles = np.array(angles)
    median_angle = float(np.median(angles))

    # Only trust a tight, consistent cluster of lines -- a scattered spread
    # means these aren't real table/text lines, so don't guess.
    if np.std(angles) > CONFIG["deskew_max_angle_std"]:
        return image
    if abs(median_angle) < CONFIG["deskew_min_angle"] or abs(median_angle) > CONFIG["deskew_angle_limit"]:
        return image

    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    return rotated

def perspective_correction_optional(image):
    orig_h, orig_w = image.shape[:2]
    ratio = 800.0 / orig_h
    small = cv2.resize(image, (int(orig_w * ratio), 800))

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 30, 100)

    cnts, _ = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return image

    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
    c = cnts[0]

    if cv2.contourArea(c) > (small.shape[0]*small.shape[1]) * CONFIG["perspective_min_area"]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)

        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = approx.reshape(4, 2) / ratio
            rect = np.zeros((4, 2), dtype="float32")
            s = pts.sum(axis=1)
            rect[0] = pts[np.argmin(s)]
            rect[2] = pts[np.argmax(s)]
            diff = np.diff(pts, axis=1)
            rect[1] = pts[np.argmin(diff)]
            rect[3] = pts[np.argmax(diff)]

            (tl, tr, br, bl) = rect
            widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
            widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
            maxWidth = max(int(widthA), int(widthB))

            heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
            heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
            maxHeight = max(int(heightA), int(heightB))

            dst = np.array([
                [0, 0],
                [maxWidth - 1, 0],
                [maxWidth - 1, maxHeight - 1],
                [0, maxHeight - 1]], dtype="float32")

            M = cv2.getPerspectiveTransform(rect, dst)
            warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
            return warped

    return image

def correct_illumination(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    bg = cv2.GaussianBlur(gray, CONFIG["illum_kernel_size"], 0)
    normalized = cv2.divide(image, cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR), scale=255)
    strength = CONFIG["illum_strength"]
    final = cv2.addWeighted(normalized, strength, image, 1.0 - strength, 0)
    return final

def upscale_if_needed(image):
    h, w = image.shape[:2]
    upscale_factor = 1.0
    if max(h, w) < CONFIG["upscale_min_dimension"]:
        upscale_factor = CONFIG["upscale_factor"]
        new_w = int(w * upscale_factor)
        new_h = int(h * upscale_factor)
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    return image

def _decode_image(image_bytes: bytes):
    """Decode bytes to a cv2 BGR image via OpenCV, falling back to PIL (handles
    progressive JPEGs better). Returns None if both fail (e.g. PDF bytes)."""
    np_arr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if image is None:
        try:
            import io
            from PIL import Image, ImageFile
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            pil_img = Image.open(io.BytesIO(image_bytes))
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            # Convert RGB array to BGR for OpenCV
            image = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception:
            return None

    return image

def apply_manual_perspective_crop(image_bytes: bytes, points: list) -> bytes:
    """Warp the image so the quadrilateral traced by `points` (4 dicts with
    fractional {x, y} in [0, 1], any order) becomes a straight rectangle.
    Falls back to the original bytes unmodified if the points are degenerate
    or anything goes wrong -- this is a best-effort correction, never a hard
    requirement for the upload to proceed.
    """
    image = _decode_image(image_bytes)
    if image is None or not points or len(points) != 4:
        return image_bytes

    try:
        h, w = image.shape[:2]
        # `points` arrives already labeled [tl, tr, br, bl] by the caller
        # (the frontend's fixed-order drag handles, or Gemini's named corner
        # fields) -- trust that order rather than re-deriving it from
        # geometric position, which can flip the result 90 degrees for a
        # document that's rotated far enough relative to the photo frame.
        pts = np.array([[p["x"] * w, p["y"] * h] for p in points], dtype="float32")

        # Guard against a degenerate/near-zero-area selection (e.g. all 4
        # points collapsed together) before attempting the warp.
        area = cv2.contourArea(pts)
        if area < 0.01 * (w * h):
            return image_bytes

        warped = warp_ordered_quad(image, pts)

        success, encoded_img = cv2.imencode('.jpg', warped, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not success:
            return image_bytes
        return encoded_img.tobytes()
    except Exception as e:
        print(f"[IMAGE ENHANCER] Manual perspective crop failed, falling back to original: {e}", flush=True)
        return image_bytes

def enhance_document_image(image_bytes: bytes, skip_perspective_detection: bool = False) -> bytes:
    """
    Takes raw image bytes, applies the scanner-like visual enhancement pipeline,
    and returns enhanced JPEG bytes.
    If the bytes cannot be read by OpenCV (e.g., they are a PDF or corrupted),
    the original bytes are safely returned unmodified.

    skip_perspective_detection: pass True when the caller already warped the
    image to a user-marked quadrilateral (apply_manual_perspective_crop). At
    that point the frame IS the document -- re-running auto boundary
    detection on it has no real edge left to find, so it just risks a second,
    unnecessary warp that softens the image or clips content (e.g. the
    rightmost column) for no benefit.
    """
    image = _decode_image(image_bytes)
    if image is None:
        return image_bytes

    try:
        # 2. Pipeline Execution
        img = auto_orient(image)
        img = deskew(img)
        if not skip_perspective_detection:
            img = perspective_correction_optional(img)
        img = correct_illumination(img)
        img = upscale_if_needed(img)

        # 3. Re-encode to JPEG
        success, encoded_img = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if success:
            enhanced_bytes = encoded_img.tobytes()
            print(f"[IMAGE ENHANCER] Successfully enhanced image! Old size: {len(image_bytes)} bytes, New size: {len(enhanced_bytes)} bytes.", flush=True)
            return enhanced_bytes
        else:
            return image_bytes
    except Exception as e:
        print(f"[IMAGE ENHANCER] Error during enhancement, falling back to original: {e}", flush=True)
        return image_bytes
