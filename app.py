# Hugging Face Spaces entry point.
#
# HF Spaces runs the file named by `app_file` in README.md (this `app.py`) with
# `python app.py` from the repo root, so the `frontend` / `explorer` package imports
# below resolve. This stays a thin wrapper around the real app in frontend/app.py, so
# local dev (`python -m frontend.app`) and the deployed Space share one implementation.
#
# CSS is passed through launch() because in Gradio 6 the page-wide CSS is a launch()
# argument, not a Blocks() argument (see frontend/app.py). HF sets the server host/port
# via env vars, which launch() reads automatically.
from frontend.app import build_app, STATUS_CSS

if __name__ == "__main__":
    build_app().launch(css=STATUS_CSS)
