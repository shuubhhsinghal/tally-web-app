import re

# Everything else this file used to hold (fetch_and_cache_masters,
# sync_tally_ledgers, sync_tally_stock_items, sync_tally_uom,
# perform_master_sync) was superseded by the connector-based sync in
# tally_reporting_sync.py / tally_reporting_stock_sync.py and had no
# remaining callers. sanitize_tally_xml is still imported from here by
# both of those files, so it's the only thing left.

def sanitize_tally_xml(raw_xml: str) -> str:
    clean_xml = re.sub(r'&#0*([0-8]|1[1-2]|1[4-9]|2[0-9]|3[0-1]);?', '', raw_xml)
    clean_xml = re.sub(r'&#x0*([0-8b-ce-f]|1[0-9a-f]);?', '', clean_xml, flags=re.IGNORECASE)
    clean_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean_xml)
    return clean_xml
