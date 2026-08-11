from PIL import Image, ImageDraw, ImageFont

def create_icon(size, filename, text="A"):
    # Teal 600 color: #0d9488
    img = Image.new('RGBA', (size, size), color='#0d9488')
    d = ImageDraw.Draw(img)
    
    # Calculate text size (approximate)
    font_size = int(size * 0.6)
    
    # Draw simple text
    try:
        # Try to use a default font
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except:
        font = ImageFont.load_default()
        
    text_bbox = d.textbbox((0, 0), text, font=font)
    w = text_bbox[2] - text_bbox[0]
    h = text_bbox[3] - text_bbox[1]
    
    x = (size - w) / 2
    y = (size - h) / 2
    
    # Optional: draw rounded rectangle for apple-touch-icon
    # But just a filled square is fine for general icons
    d.text((x, y - h*0.2), text, font=font, fill='white')
    img.save(f"frontend/public/{filename}")

import os
os.makedirs("frontend/public/icons", exist_ok=True)
create_icon(192, "icons/icon-192.png")
create_icon(512, "icons/icon-512.png")
create_icon(180, "icons/apple-touch-icon.png")
print("Icons generated!")
