import cv2
import numpy as np
import pytesseract
import re

# ==========================================
# EXPERIMENTAL PARAMETERS
# ==========================================
CONFIG = {
    # Orientation
    "osd_confidence_threshold": 1.0,
    
    # Safe Crop
    "blur_kernel_size": (5, 5),
    "canny_threshold1": 30,
    "canny_threshold2": 100,
    "crop_safety_margin": 0.05, 
    
    # Deskew
    "deskew_angle_limit": 10.0,
    
    # Perspective
    "perspective_min_area": 0.5, 
    
    # Illumination Correction
    "illum_kernel_size": (101, 101), # Large kernel for background estimation
    "illum_strength": 0.8,           # How strongly to apply illumination correction
    
    # Contrast
    "clahe_clip_limit": 2.5,
    "clahe_tile_grid_size": (8, 8),
    "global_contrast_alpha": 1.1,    # Mild global contrast (1.0 = none)
    "global_contrast_beta": -10,     # Brightness shift
    
    # Sharpening
    "sharpen_amount": 2.0,           # Stronger unsharp mask amount
    "sharpen_radius": 2.0,           # Gaussian blur sigma for unsharp mask
    
    # Upscale
    "upscale_min_dimension": 2000,
    "upscale_factor": 1.5,
    
    # Adaptive Threshold (B&W Filter)
    "thresh_block_size": 41,
    "thresh_c": 15,
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

def safe_document_crop(image):
    orig_h, orig_w = image.shape[:2]
    ratio = 800.0 / orig_h
    small = cv2.resize(image, (int(orig_w * ratio), 800))
    
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, CONFIG["blur_kernel_size"], 0)
    edged = cv2.Canny(blurred, CONFIG["canny_threshold1"], CONFIG["canny_threshold2"])
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)
    
    cnts, _ = cv2.findContours(closed.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return image
        
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
    largest_c = cnts[0]
    
    img_area = small.shape[0] * small.shape[1]
    largest_area = cv2.contourArea(largest_c)
    
    if largest_area > img_area * 0.15:
        x, y, w, h = cv2.boundingRect(largest_c)
        x, y, w, h = int(x / ratio), int(y / ratio), int(w / ratio), int(h / ratio)
        
        mx = int(w * CONFIG["crop_safety_margin"])
        my = int(h * CONFIG["crop_safety_margin"])
        
        x1 = max(0, x - mx)
        y1 = max(0, y - my)
        x2 = min(orig_w, x + w + mx)
        y2 = min(orig_h, y + h + my)
        
        crop_w = x2 - x1
        crop_h = y2 - y1
        
        if crop_w * crop_h > orig_w * orig_h * 0.95:
            return image
            
        cropped = image[y1:y2, x1:x2]
        return cropped
        
    return image

def deskew(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 2))
    dilated = cv2.dilate(thresh, kernel, iterations=1)
    
    coords = np.column_stack(np.where(dilated > 0))
    if len(coords) == 0:
        return image
        
    angle = cv2.minAreaRect(coords)[-1]
    
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
        
    if abs(angle) > CONFIG["deskew_angle_limit"] or abs(angle) < 0.1:
        return image
        
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
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

def enhance_contrast(image):
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    
    alpha = CONFIG["global_contrast_alpha"]
    beta = CONFIG["global_contrast_beta"]
    l = cv2.convertScaleAbs(l, alpha=alpha, beta=beta)
    
    clahe = cv2.createCLAHE(clipLimit=CONFIG["clahe_clip_limit"], tileGridSize=CONFIG["clahe_tile_grid_size"])
    cl = clahe.apply(l)
    
    limg = cv2.merge((cl, a, b))
    final = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
    return final

def sharpen(image):
    amount = CONFIG["sharpen_amount"]
    radius = CONFIG["sharpen_radius"]
    
    blurred = cv2.GaussianBlur(image, (0, 0), radius)
    sharpened = cv2.addWeighted(image, 1.0 + amount, blurred, -amount, 0)
    return sharpened

def upscale_if_needed(image):
    h, w = image.shape[:2]
    upscale_factor = 1.0
    if max(h, w) < CONFIG["upscale_min_dimension"]:
        upscale_factor = CONFIG["upscale_factor"]
        new_w = int(w * upscale_factor)
        new_h = int(h * upscale_factor)
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    return image

def apply_bw_threshold(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Use a slightly higher C value (25) to clear out more speckle noise
    thresh_c = CONFIG.get("thresh_c", 25)
    
    thresh = cv2.adaptiveThreshold(
        gray, 255, 
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 
        CONFIG.get("thresh_block_size", 41), 
        thresh_c
    )
    
    # Convert back to 3-channel BGR
    return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

def enhance_document_image(image_bytes: bytes) -> bytes:
    """
    Takes raw image bytes, applies the scanner-like visual enhancement pipeline,
    and returns enhanced JPEG bytes.
    If the bytes cannot be read by OpenCV (e.g., they are a PDF or corrupted),
    the original bytes are safely returned unmodified.
    """
    # 1. Try to decode the bytes as an image with OpenCV
    np_arr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    
    # If OpenCV fails, try PIL (it handles progressive JPEGs better)
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
            # If both fail (e.g., PDF or corrupted bytes), return original
            return image_bytes

    try:
        # 2. Pipeline Execution
        img = auto_orient(image)
        img = safe_document_crop(img)
        img = deskew(img)
        img = perspective_correction_optional(img)
        img = correct_illumination(img)
        img = enhance_contrast(img)
        img = sharpen(img)
        img = apply_bw_threshold(img)
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
