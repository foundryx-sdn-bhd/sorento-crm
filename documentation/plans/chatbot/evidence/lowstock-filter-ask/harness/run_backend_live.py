"""Cloud browser pass launcher, REAL parser path: the real backend, only storage swapped
for local disk (no S3 credentials).

LLM-TEST-COST: the parser answers from recorded cassettes by default
(LLM_CASSETTE_MODE=replay, files under LLM_CASSETTE_DIR, outside git). A turn with no
recording fails with an error naming the missing key; it never calls OpenAI.

    python run_backend_live.py            # replay (default)
    python run_backend_live.py --record   # call OpenAI through the agent proxy, record
    python run_backend_live.py --live     # call OpenAI, record nothing
"""
import os, sys
sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services import llm_cassette  # noqa: E402

print(f"LLM_CASSETTE_MODE={llm_cassette.configure_runner(sys.argv[1:])}", flush=True)
import local_storage  # noqa: F401,E402
from app.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
