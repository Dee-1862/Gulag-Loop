"""Where large local files live (Chrome profile, recordings, OCR models, frames).

Rishit's laptop keeps them on D:/czb. Elsewhere they go to ./local_data (gitignored).
Override with the CZB_DIR environment variable.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CZB_DIR") or ("D:/czb" if Path("D:/czb").exists() else ROOT / "local_data"))


def data_path(*parts, mkdir_parent=True):
    """Absolute path under DATA_DIR, as a forward-slash string; creates the parent folder."""
    p = DATA_DIR.joinpath(*parts)
    if mkdir_parent:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p.as_posix()


def resolve_weights(configured):
    """The configured detector weights if present, else the first local copy that exists.

    A TensorRT engine is skipped when the .pt next to it is newer: it was built from older weights.
    Rebuild it with `python scripts/check_setup.py --export-engine`.
    """
    candidates = [configured, data_path("models", "zombies_v1.engine", mkdir_parent=False),
                  str(ROOT / "model" / "zombies_v1.engine"), str(ROOT / "model" / "zombies_v1.pt")]
    for c in candidates:
        if not c or not Path(c).exists():
            continue
        p = Path(c)
        pt = p.with_suffix(".pt")
        if p.suffix == ".engine" and pt.exists() and pt.stat().st_mtime > p.stat().st_mtime:
            print(f"[weights] {p.name} is older than {pt.name}; skipping the stale engine")
            continue
        return str(c)
    raise FileNotFoundError(f"No detector weights found; tried {candidates}")
