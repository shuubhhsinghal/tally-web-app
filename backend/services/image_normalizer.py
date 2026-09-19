import io
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()

def normalize_uploaded_invoice(file_bytes: bytes, filename: str, content_type: str) -> bytes:
    """
    Detects if the uploaded bytes are HEIC/HEIF based on filename extension or MIME type.
    If so, decodes, corrects EXIF orientation, and converts to JPEG bytes.
    For other raster images, corrects EXIF orientation in place if a non-default
    orientation tag is present (needed so pixel coordinates computed downstream --
    e.g. a manually marked crop -- agree with what the user saw on screen, since
    browsers auto-rotate for display but don't strip the tag from the file itself).
    PDFs and anything else pass through unmodified.
    """
    filename_lower = (filename or "").lower()
    content_type_lower = (content_type or "").lower()

    is_heic_extension = filename_lower.endswith(".heic") or filename_lower.endswith(".heif")
    is_heic_mime = "image/heic" in content_type_lower or "image/heif" in content_type_lower

    if is_heic_extension or is_heic_mime:
        try:
            img = Image.open(io.BytesIO(file_bytes))

            # Apply EXIF orientation
            img = ImageOps.exif_transpose(img)

            # Ensure RGB format
            if img.mode != "RGB":
                img = img.convert("RGB")

            out_buffer = io.BytesIO()
            img.save(out_buffer, format="JPEG", quality=95)
            return out_buffer.getvalue()
        except UnidentifiedImageError:
            raise HTTPException(status_code=400, detail="Unable to read this HEIC/HEIF image. Please upload a valid invoice image.")
        except Exception:
            raise HTTPException(status_code=400, detail="Unable to read this HEIC/HEIF image. Please upload a valid invoice image.")

    is_pdf = filename_lower.endswith(".pdf") or "application/pdf" in content_type_lower
    if is_pdf:
        return file_bytes

    try:
        img = Image.open(io.BytesIO(file_bytes))
        orientation = img.getexif().get(0x0112, 1)  # 0x0112 = EXIF Orientation tag
        if orientation and orientation != 1:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            out_buffer = io.BytesIO()
            img.save(out_buffer, format="JPEG", quality=95)
            return out_buffer.getvalue()
    except Exception:
        # Not a readable raster image (or no EXIF support) -- leave untouched.
        pass

    return file_bytes
