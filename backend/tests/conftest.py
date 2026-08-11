import os
import pytest
import sqlite3

# Set the TESTING environment variable before any application code is imported.
# This ensures that backend.database.DB_PATH points to the test database.
os.environ["TESTING"] = "true"

from backend.database import DB_PATH, init_db

@pytest.fixture(scope="session", autouse=True)
def setup_test_db_session():
    # Ensure any previous test database is removed
    if os.path.exists(DB_PATH) and "test_tally_sync" in DB_PATH:
        os.remove(DB_PATH)
        
    # Initialize the test database schema
    init_db()
    
    yield
    
    # Cleanup test database after all tests complete
    if os.path.exists(DB_PATH) and "test_tally_sync" in DB_PATH:
        os.remove(DB_PATH)
