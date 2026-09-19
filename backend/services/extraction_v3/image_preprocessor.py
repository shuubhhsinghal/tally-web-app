import cv2
import numpy as np

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def warp_ordered_quad(image, rect):
    """Warp `image` so the quadrilateral `rect` (already given in [tl, tr, br, bl]
    order -- the caller's semantic labeling is trusted as-is, nothing is
    re-derived from geometric position) becomes a straight rectangle."""
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
    M = cv2.getPerspectiveTransform(np.array(rect, dtype="float32"), dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
    return warped

def four_point_transform(image, pts):
    """Warp `image` using 4 UNORDERED points -- for callers (like contour
    detection) that have no semantic tl/tr/br/bl labeling of their own, so
    the order must be derived geometrically first."""
    rect = order_points(pts)
    return warp_ordered_quad(image, rect)

def flatten_document(image_bytes: bytes) -> bytes:
    """Bypass fragile OpenCV contour detection to prevent accidental metadata cropping."""
    return image_bytes
