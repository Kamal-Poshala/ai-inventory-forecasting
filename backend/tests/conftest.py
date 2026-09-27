import os
import tempfile

# Must be set before the app (and its engine) is imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ANTHROPIC_API_KEY"] = ""
