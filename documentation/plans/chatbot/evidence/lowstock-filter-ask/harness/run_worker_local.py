"""Worker launcher for the cloud browser pass: storage swapped for local disk.

LLM-TEST-COST: same cassette switch as `run_backend_live.py` (replay by default,
`--record` / `--live` to spend). A worker job on a provider the cassettes do not cover
(Anthropic, Gemini) is refused in replay rather than paid for.
"""
import sys, os
sys.path.insert(0, "."); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services import llm_cassette  # noqa: E402

print(f"LLM_CASSETTE_MODE={llm_cassette.configure_runner(sys.argv[1:])}", flush=True)
sys.argv = [a for a in sys.argv if a not in ("--live", "--record")]
import local_storage  # noqa: F401,E402
import runpy  # noqa: E402
runpy.run_path("worker.py", run_name="__main__")
