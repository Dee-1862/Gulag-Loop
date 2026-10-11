"""Check a game laptop before the first real run. Never moves the mouse or opens the game.

    .venv\\Scripts\\python scripts\\check_setup.py
    .venv\\Scripts\\python scripts\\check_setup.py --export-engine   # also build a TensorRT engine for this GPU
"""
import argparse
import importlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append(True)
        print(f"PASS {name}" + (f"  ({detail})" if detail else ""))
        return detail
    except Exception as e:
        results.append(False)
        print(f"FAIL {name}  ({type(e).__name__}: {str(e)[:200]})")


def gpu():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError(f"torch {torch.__version__} has no CUDA")
    return f"torch {torch.__version__}, {torch.cuda.get_device_name(0)}"


def packages():
    missing = []
    for m in ("ultralytics", "dxcam", "pydirectinput", "keyboard", "playwright", "easyocr", "yaml", "cv2"):
        try:
            importlib.import_module(m)
        except Exception as e:
            missing.append(f"{m} ({type(e).__name__})")
    if missing:
        raise RuntimeError("missing: " + ", ".join(missing))
    return "ultralytics, dxcam, pydirectinput, keyboard, playwright, easyocr"


def detector():
    import cv2
    import yaml
    from common.paths import resolve_weights
    from vision.pipeline import VisionPipeline
    weights = resolve_weights(yaml.safe_load(open("config.yaml"))["vision"]["weights"])
    vp = VisionPipeline.from_config("config.yaml", live=False, weights=weights)
    frame = cv2.resize(cv2.imread(str(ROOT / "docs" / "img" / "port_bot_kill.jpg")), (1280, 720))
    for _ in range(3):  # warm-up
        vp.step(0.3, "nearest", 0.15, frame=frame)
    t0 = time.perf_counter()
    for _ in range(20):
        obs = vp.step(0.3, "nearest", 0.15, frame=frame)
    ms = (time.perf_counter() - t0) / 20 * 1000
    return (f"{Path(weights).name} on {vp.device_name}: {ms:.1f} ms/frame, "
            f"{obs.telemetry['n_boxes']} zombie box(es) on a port screenshot")


def chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    raise FileNotFoundError("Google Chrome not found (bot/game.py launches channel='chrome')")


def mongo():
    from common.db import get_db
    db = get_db()
    db.command("ping")
    return f"{len(db.list_collection_names())} collections in {db.name}"


def luminal():
    from luminal_client import load_config
    cfg = load_config(ROOT / ".env")
    if not cfg.get("LUMINAL_API_KEY"):
        raise RuntimeError("LUMINAL_API_KEY missing from .env")
    return f"key set, model {cfg.get('LUMINAL_MODEL')}"


def calibration():
    if not (ROOT / "calibration.json").exists():
        raise FileNotFoundError("run `python -m bot.worker calibrate` with the game (takes the mouse)")
    return (ROOT / "calibration.json").read_text().strip().replace("\n", " ")[:120]


def export_engine():
    from ultralytics import YOLO
    src = ROOT / "model" / "zombies_v1.pt"
    out = YOLO(str(src)).export(format="engine", half=True, imgsz=640)
    return f"built {out}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-engine", action="store_true")
    args = ap.parse_args()
    check("CUDA GPU", gpu)
    check("game + vision packages", packages)
    check("Chrome installed", chrome)
    check("MongoDB reachable", mongo)
    check("Luminal configured", luminal)
    if args.export_engine:
        check("TensorRT engine for this GPU", export_engine)
    check("detector runs on a real port frame", detector)
    check("mouse calibration", calibration)
    print(f"\n{sum(results)}/{len(results)} checks passed")
