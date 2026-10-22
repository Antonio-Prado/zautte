"""Settings the tests rely on; set before any module reads them."""
import os
import sys
from pathlib import Path

os.environ.setdefault("PII_BLOCK", "true")
os.environ.setdefault("PII_REDACTION", "true")
os.environ["CRAWL_ALLOWED_DOMAINS"] = "example.org"
os.environ["MIGRATED_DOMAINS"] = "old.example.org,portal.example.org/legacy"
os.environ["DATA_DIR"] = os.environ.get("DATA_DIR", "/tmp/zautte-test-data")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
