import os
from dotenv import load_dotenv

load_dotenv()

TALLY_URL = os.environ.get("TALLY_URL", "http://100.107.220.58:9000")
