import re
import xml.etree.ElementTree as ET

def sanitize_tally_xml(raw_xml: str) -> str:
    clean_xml = re.sub(r'&#0*([0-8]|1[1-2]|1[4-9]|2[0-9]|3[0-1]);?', '', raw_xml)
    clean_xml = re.sub(r'&#x0*([0-8b-ce-f]|1[0-9a-f]);?', '', clean_xml, flags=re.IGNORECASE)
    clean_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean_xml)
    return clean_xml

def parse_tally_response(raw_xml: str, operation_type: str) -> dict:
    result = {
        "is_success": False,
        "error_message": None,
        "created": 0,
        "altered": 0,
        "errors": 0,
        "line_errors": []
    }
    
    try:
        clean_xml = sanitize_tally_xml(raw_xml)
        root = ET.fromstring(clean_xml)
    except ET.ParseError:
        result["error_message"] = "Malformed XML response from Tally."
        return result
        
    created_elem = root.find('.//CREATED')
    if created_elem is not None and created_elem.text:
        try:
            result["created"] = int(created_elem.text)
        except ValueError:
            pass
            
    altered_elem = root.find('.//ALTERED')
    if altered_elem is not None and altered_elem.text:
        try:
            result["altered"] = int(altered_elem.text)
        except ValueError:
            pass
            
    errors_elem = root.find('.//ERRORS')
    if errors_elem is not None and errors_elem.text:
        try:
            result["errors"] = int(errors_elem.text)
        except ValueError:
            pass
            
    for line_error in root.findall('.//LINEERROR'):
        if line_error.text:
            result["line_errors"].append(line_error.text.strip())
            
    if result["line_errors"]:
        has_unapproved_error = False
        for error_msg in result["line_errors"]:
            if "already exists" in error_msg.lower() and operation_type in ["CREATE_UOM", "CREATE_ITEM", "CREATE_LEDGER"]:
                continue
            has_unapproved_error = True
            
        if has_unapproved_error:
            result["is_success"] = False
            result["error_message"] = " | ".join(result["line_errors"])
            return result
        else:
            result["is_success"] = True
            return result

    if result["errors"] > 0:
        result["is_success"] = False
        result["error_message"] = f"Tally reported ERRORS={result['errors']} without a LINEERROR message."
        return result
            
    if result["created"] > 0 or result["altered"] > 0:
        result["is_success"] = True
        return result
        
    result["is_success"] = False
    result["error_message"] = f"Tally response did not indicate success (CREATED={result['created']}, ALTERED={result['altered']}, ERRORS={result['errors']})."
    return result

def is_already_exists_success(parsed_result: dict, operation_type: str) -> bool:
    """
    Returns True if the parsed result is successful specifically because of the 
    narrow 'already exists' exception for masters.
    """
    if not parsed_result.get("is_success"):
        return False
    if operation_type not in ["CREATE_UOM", "CREATE_ITEM", "CREATE_LEDGER"]:
        return False
    # If it was successful but has line_errors containing 'already exists'
    line_errors = parsed_result.get("line_errors", [])
    return any("already exists" in e.lower() for e in line_errors)
