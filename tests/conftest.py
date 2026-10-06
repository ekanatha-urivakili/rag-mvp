import os
import secrets

# Test settings must be in place before any `rag` module reads them.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
os.environ["ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
