"""Publish the parser prompt's wording layer on a database that will not run alembic data
migrations (crew's hand-test copy). The standalone twin of
`alembic/versions/pdyn_0002_wording_layer.py::apply`: idempotent, never moves a label.

    venv/bin/python -m scripts.publish_parser_wording_layer

Prints the new version number and the transform's report, or that it already exists.
"""
from __future__ import annotations

import importlib.util
import pathlib


def main() -> None:
    import app.main  # noqa: F401  registers every model
    from app.database import engine

    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "pdyn_0002_wording_layer.py"
    spec = importlib.util.spec_from_file_location("_pdyn_0002_wording_layer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        version = module.apply(conn)
    if version is None:
        print("wording layer already published (or no production version); nothing done")
    else:
        print(f"published chatbot_semantic_parser v{version} (unlabelled); promote it on the Prompts page")


if __name__ == "__main__":
    main()
