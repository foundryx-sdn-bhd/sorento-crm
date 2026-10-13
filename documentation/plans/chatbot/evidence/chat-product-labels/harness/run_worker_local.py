import sys, os
sys.path.insert(0, "."); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_storage  # noqa: F401
import runpy
runpy.run_path("worker.py", run_name="__main__")
