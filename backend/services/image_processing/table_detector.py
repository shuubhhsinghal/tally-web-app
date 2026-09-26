import cv2
import pytesseract
import numpy as np
import shutil
import logging

# Dynamically discover Tesseract path for cross-platform compatibility (macOS/Linux)
tesseract_path = shutil.which('tesseract')
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
else:
    logging.warning("Tesseract executable not found in system PATH. Ensure it is installed.")

def crop_item_table(image_bytes: bytes) -> bytes:
    """Finds the table header and footer, and crops the image strictly to the items."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return image_bytes
    
    # Get bounding boxes for all text
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    
    top_y = 0
    bottom_y = img.shape[0]
    
    # 1. Find the top boundary (Table Header)
    for i, word in enumerate(d['text']):
        clean_word = word.strip().lower()
        if clean_word in ["description", "goods", "item", "particulars"]:
            # Set the crop line just above the header text so LLM can see column names
            top_y = max(0, d['top'][i] - 10)
            break
            
    # 2. Find the bottom boundary (Tax/Totals)
    for i, word in enumerate(d['text']):
        clean_word = word.strip().lower()
        if clean_word in ["sgst", "cgst", "subtotal", "total", "round"]:
            # Set the crop line just above the footer text (with generous margin to not slice items)
            # Only accept it if it's in the bottom half of the page to avoid false positives
            if d['top'][i] > (img.shape[0] / 3):
                bottom_y = d['top'][i] + 30
                break
                
    # 3. Crop the image
    # If the boundaries make sense, crop. Otherwise, fallback to the original image.
    if top_y < bottom_y and (bottom_y - top_y) > 100:
        cropped_img = img[top_y:bottom_y, 0:img.shape[1]]
    else:
        cropped_img = img

    # Return the cropped bytes
    _, buffer = cv2.imencode('.jpg', cropped_img)
    return buffer.tobytes()
