import os
import tempfile

# Point the app at a throwaway database before any app module is imported.
os.environ["FAULTLINES_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DETECTION_ENGINE"] = "rules"
os.environ["DEMO_PASSWORD"] = "test-password"
