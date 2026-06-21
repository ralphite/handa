import os

# The Web app refreshes the introspection export (a subprocess that imports
# the full agent/tool stack) on startup; suppress it suite-wide so dozens of
# create_app() tests don't each fork a heavyweight exporter.
os.environ.setdefault("HANDA_DISABLE_INTROSPECTION_REFRESH", "1")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
