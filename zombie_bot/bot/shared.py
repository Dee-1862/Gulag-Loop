"""Team-wide calibration and strategies from MongoDB (common/machines.py), with local-file fallback.

Works from the repo and from the standalone zombie_bot/ bundle: it looks upward for common/machines.py.
If MongoDB or the repo isn't available, every function returns None and callers use their local files.
"""
import os
import sys


def _machines():
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (os.path.join(here, ".."), os.path.join(here, "..", "..")):
        root = os.path.abspath(up)
        if os.path.exists(os.path.join(root, "common", "machines.py")):
            if root not in sys.path:
                sys.path.append(root)  # append: never shadow this package's own bot/ or vision/
            try:
                from common import machines
                return machines
            except Exception:
                return None
    return None


def team_calibration(machine=None):
    """{'counts_per_px', 'source'} for this laptop (or the team default), or None."""
    m = _machines()
    try:
        return m.get_calibration(machine) if m else None
    except Exception:
        return None


def save_team_calibration(counts_per_px, machine=None, **extra):
    m = _machines()
    try:
        return m.save_calibration(counts_per_px, machine, **extra) if m else None
    except Exception:
        return None


def team_strategy(name, map_name="kino"):
    """{'params', 'source', ...} for a named strategy or 'champion', or None."""
    m = _machines()
    try:
        return m.get_strategy(name, map_name) if m else None
    except Exception:
        return None
