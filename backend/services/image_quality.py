import cv2
import numpy as np
import logging
import io
from PIL import Image, UnidentifiedImageError, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


logger = logging.getLogger(__name__)

# Configurable constants for initial experimental thresholds
# Note: UNUSABLE is kept very conservative. A low laplacian score alone won't reject if resolution is decent.
LAPLACIAN_BORDERLINE_THRESH = 85.0
LAPLACIAN_UNUSABLE_THRESH = 15.0
MIN_USABLE_RESOLUTION = 800  # If both dimensions are smaller than this, it's considered tiny
MIN_UNUSABLE_RESOLUTION = 400 # If both dimensions are smaller than this, it's absolutely unusable

def assess_and_enhance_image(image_bytes: bytes) -> tuple[bytes, str, dict]:
    """
    Assesses image quality using OpenCV and selectively applies enhancement if BORDERLINE.
    Returns: (output_bytes, status, metrics)
    """
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        # Convert PIL to OpenCV format (BGR)
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except UnidentifiedImageError:
        return image_bytes, "GOOD", {"format": "unsupported/pdf", "bypassed": True}
    except Exception as e:
        logger.error(f"Failed to decode image in quality gate: {e}")
        return image_bytes, "GOOD", {"format": "error", "bypassed": True}
        
    height, width = img.shape[:2]
    
    # Calculate Variance of Laplacian (blur metric)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    metrics = {
        "original_width": width,
        "original_height": height,
        "blur_score": round(laplacian_var, 2),
        "preprocessed": False,
        "operations": []
    }
    
    status = "GOOD"
    
    is_tiny = width < MIN_UNUSABLE_RESOLUTION and height < MIN_UNUSABLE_RESOLUTION
    is_small = width < MIN_USABLE_RESOLUTION and height < MIN_USABLE_RESOLUTION
    is_extremely_blurry = laplacian_var < LAPLACIAN_UNUSABLE_THRESH
    is_somewhat_blurry = laplacian_var < LAPLACIAN_BORDERLINE_THRESH
    
    # Conservative UNUSABLE check: Must be extremely blurry AND small, or just impossibly tiny.
    if is_tiny or (is_extremely_blurry and is_small):
        status = "UNUSABLE"
    elif is_somewhat_blurry or is_small:
        status = "BORDERLINE"
        
    if status == "UNUSABLE":
        logger.warning(f"Image rejected as UNUSABLE: {metrics}")
        return image_bytes, status, metrics
        
    if status == "BORDERLINE":
        # Apply conservative enhancements
        enhanced_img = img.copy()
        
        # 1. Upscale if small
        if max(width, height) < 1200:
            scale = 1200 / max(width, height)
            new_w = int(width * scale)
            new_h = int(height * scale)
            enhanced_img = cv2.resize(enhanced_img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
            metrics["operations"].append(f"upscale_{scale:.2f}x")
            
        # 2. LAB CLAHE for contrast while preserving color
        lab = cv2.cvtColor(enhanced_img, cv2.COLOR_BGR2LAB)
        l_channel, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl = clahe.apply(l_channel)
        merged_lab = cv2.merge((cl, a, b))
        enhanced_img = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
        metrics["operations"].append("clahe_lightness")
        
        # 3. Mild Unsharp Masking
        blurred = cv2.GaussianBlur(enhanced_img, (0, 0), 2.0)
        enhanced_img = cv2.addWeighted(enhanced_img, 1.5, blurred, -0.5, 0)
        metrics["operations"].append("unsharp_mask")
        
        metrics["preprocessed"] = True
        metrics["final_width"] = enhanced_img.shape[1]
        metrics["final_height"] = enhanced_img.shape[0]
        
        # Encode back to JPEG
        _, buffer = cv2.imencode('.jpg', enhanced_img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        output_bytes = buffer.tobytes()
        
        logger.info(f"Image enhanced (BORDERLINE): {metrics}")
        return output_bytes, status, metrics
        
    logger.info(f"Image passed unmodified (GOOD): {metrics}")
    return image_bytes, status, metrics
