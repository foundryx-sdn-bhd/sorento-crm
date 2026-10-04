"""Cloud browser pass launcher, LIVE parser: the real backend, only storage swapped for
local disk (no S3 credentials). The parser calls OpenAI through the sandbox agent proxy."""
import os, sys
sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_storage  # noqa: F401,E402
from app.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
