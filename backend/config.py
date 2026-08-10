import os
from dotenv import load_dotenv

load_dotenv()

TALLY_URL = os.environ.get("TALLY_URL", "http://100.90.163.23:9000")
