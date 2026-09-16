import io
import logging
from PIL import Image, ImageFile, UnidentifiedImageError

ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger(__name__)

def normalize_to_pdf(images: list[bytes]) -> tuple[bytes, str, str]:
    """
    Normalizes a list of images into a single PDF document representation.
    
    If the input is a single PDF, returns it unchanged.
    If the input is multiple PDFs, logs a warning and returns the first one.
    If the input is raster images (JPEG, PNG, WebP), combines them into a single multi-page PDF.
    
    Returns:
        (document_bytes, mime_type, file_suffix)
    """
    if not images:
        raise ValueError("No images provided for normalization.")

    # Check if the first image is already a PDF
    is_pdf = [img.startswith(b"%PDF") for img in images]
    
    if any(is_pdf):
        if not all(is_pdf):
            logger.warning("Mixed PDF and raster images found. Using the first PDF and ignoring rasters.")
        
        pdf_idx = is_pdf.index(True)
        return images[pdf_idx], "application/pdf", ".pdf"

    # All images are raster images. Combine them into a single PDF.
    pil_images = []
    for i, img_bytes in enumerate(images):
        try:
            pil_img = Image.open(io.BytesIO(img_bytes))
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            pil_images.append(pil_img)
        except UnidentifiedImageError:
            logger.error(f"Failed to decode image at index {i}. Passing through original bytes.")
            return images[0], "image/jpeg", ".jpg" 
        except Exception as e:
            logger.error(f"Error processing image at index {i}: {e}")
            return images[0], "image/jpeg", ".jpg"
            
    if not pil_images:
        return images[0], "image/jpeg", ".jpg"
        
    pdf_bytes_io = io.BytesIO()
    try:
        if len(pil_images) == 1:
            pil_images[0].save(pdf_bytes_io, format="PDF", resolution=100.0)
        else:
            pil_images[0].save(
                pdf_bytes_io, 
                format="PDF", 
                save_all=True, 
                append_images=pil_images[1:], 
                resolution=100.0
            )
        return pdf_bytes_io.getvalue(), "application/pdf", ".pdf"
    except Exception as e:
        logger.error(f"Failed to generate PDF from images: {e}")
        # Fallback to the first original image
        return images[0], "image/jpeg", ".jpg"
