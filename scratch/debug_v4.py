import os
import sys
sys.path.append(os.getcwd())
import asyncio
from unittest.mock import patch, MagicMock
from backend.services.extraction_v4.extraction_engine import process_invoice_v4, call_gemini_extraction_v4
from backend.services.extraction_v4.metadata_extractor import call_metadata_extraction_v4

def mock_generate_content(*args, **kwargs):
    print(f"\n[MOCK GENERATE CONTENT CALLED]")
    print(f"Model: {kwargs.get('model')}")
    contents = kwargs.get('contents', [])
    print(f"Contents count: {len(contents)}")
    for i, item in enumerate(contents):
        if isinstance(item, str):
            print(f"Item {i} is String prompt. Length: {len(item)}")
        else:
            print(f"Item {i} is File/Image. Type: {type(item)}")
    
    # Return dummy response
    mock_res = MagicMock()
    mock_res.text = '{"items": [], "detected_headers": []}'
    return mock_res

@patch("google.genai.Client")
def main(mock_client):
    mock_instance = MagicMock()
    mock_client.return_value = mock_instance
    mock_instance.models.generate_content = mock_generate_content
    
    mock_file1 = MagicMock()
    mock_file1.name = "mock_file_1"
    mock_file2 = MagicMock()
    mock_file2.name = "mock_file_2"
    
    def mock_upload(file, **kwargs):
        return mock_file1 if "1" in file else mock_file2
        
    mock_instance.files.upload = mock_upload
    
    images = [b"mock_bytes_1", b"mock_bytes_2"]
    
    print("Running process_invoice_v4 with 2 images...")
    
    # We also need to mock crop_item_table so it doesn't fail on mock bytes
    with patch("backend.services.extraction_v4.extraction_engine.crop_item_table", return_value=b"cropped_1"):
        with patch("backend.services.extraction_v4.extraction_engine.call_metadata_extraction_v4", return_value={"physical_row_count": 0}):
            res = process_invoice_v4(images)
            
    print("\nExtraction complete.")

if __name__ == "__main__":
    main()
