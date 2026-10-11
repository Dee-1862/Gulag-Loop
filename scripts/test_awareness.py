"""Offline test of the controller's awareness: heading, zombie memory, damage turn, entry points, recoil.

    .venv\\Scripts\\python scripts\\test_awareness.py

Fakes the mouse, keyboard, game, capture and detector; nothing touches the real mouse.
Writes local_data/awareness_test.mp4 and one frame of the compass HUD to local_data/awareness_frame.jpg.
"""
import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

moves = []
fake_pdi = types.SimpleNamespace(FAILSAFE=False, PAUSE=0,
                                 moveRel=lambda dx, dy, **kw: moves.append((dx, dy)),
                                 keyDown=lambda k: None, keyUp=lambda k: None, press=lambda k: None,
                                 mouseDown=lambda: None, mouseUp=lambda: None)
sys.modules["pydirectinput"] = fake_pdi
sys.modules["keyboard"] = types.SimpleNamespace(add_hotkey=lambda *a, **k: 1, remove_hotkey=lambda h: None)

import bot.controller as controller  # noqa: E402
from common.db import get_db  # noqa: E402
from vision.detector import Box  # noqa: E402

MAP = "test-awareness"
step = {"i": 0}


class FakeCapture:
    def __init__(self, *a, **k):
        pass

    def grab(self):
        i = step["i"]
        frame = np.full((720, 1280, 3), 30, np.uint8)
        if 40 <= i <= 42:  # damage flash: red screen edges
            frame[:90, :] = (20, 20, 200)
            frame[-90:, :] = (20, 20, 200)
            frame[:, :150] = (20, 20, 200)
            frame[:, -150:] = (20, 20, 200)
        return frame

    def close(self):
        pass


class FakeVision:
    fps = 30.0

    def __init__(self):
        self.tracker = types.SimpleNamespace()

    def step(self, conf, rule, aim_y_offset, frame=None, ego=None):
        i = step["i"]
        step["i"] += 1
        tracks, target = [], None
        if 5 <= i <= 20:  # one zombie, far, drifting left until it leaves the screen
            x = 700 - 40 * (i - 5)
            if x > 0:
                b = Box(x - 20, 300, x + 20, 400, 0.8)
                t = types.SimpleNamespace(id=1, box=b, vx=0, vy=0, trail=[], hits=5, missed=0)
                tracks, target = [t], t
        obs = types.SimpleNamespace(tracks=tracks, target=target, lock_frames=5 if target else 0,
                                    err_x=(target.box.cx - 640) if target else 0.0,
                                    err_y=(target.box.y1 + aim_y_offset * target.box.h - 360) if target else 0.0,
                                    nearest_h_frac=(target.box.h / 720) if target else 0.0,
                                    telemetry={"n_boxes": len(tracks), "n_tracks": len(tracks)})
        return obs

    def hud_frame(self, **kw):
        return np.full((720, 1280, 3), 30, np.uint8)


class FakeHud:
    def __init__(self):
        self.calls = 0

    def read_points(self, frame):
        self.calls += 1
        return 500 if self.calls < 3 else 600  # +100 = a headshot kill

    def read_ammo(self, frame):
        return (8, 32)

    def game_over(self, frame):
        return False


class FakeGame:
    def locked(self):
        return True

    def ensure_locked(self):
        return True

    def canvas_region(self):
        return (0, 0, 1280, 720)


def main():
    import vision.capture
    vision.capture.Capture = FakeCapture
    db = get_db()
    db["entry_events"].delete_many({"map": MAP})
    ok = True
    try:
        res = controller.run_episode(FakeGame(), FakeVision(), FakeHud(), dict(controller.BASELINE), max_s=3.0,
                                     counts_per_px=0.6794, record_path=str(ROOT / "local_data" / "awareness_test.mp4"),
                                     save_frames_dir=None, log=print, cap=FakeCapture(), map_name=MAP, db=db,
                                     experiment_id="test", job_id="test-job")
        d = res["diagnostics"]
        big_turns = [m for m in moves if abs(m[0]) >= 100]
        saved = db["entry_events"].count_documents({"map": MAP})
        checks = [
            ("zombie memory counted an active zombie", d["max_active_zombies"] >= 1),
            ("damage flash detected", d["damage_events"] >= 1),
            ("hit with nothing visible -> unseen hit", d["unseen_hits"] >= 1),
            ("bot turned around fast after the hit (EVADE)", len(big_turns) >= 1),
            ("entry sightings saved to MongoDB", saved >= 1),
            ("+100 points classified as a headshot kill", d["headshot_kills_est"] == 1),
        ]
        for name, good in checks:
            print(("PASS " if good else "FAIL ") + name)
            ok &= good
        print("diagnostics:", {k: d[k] for k in ("max_active_zombies", "avg_active_zombies", "damage_events",
                                                 "unseen_hits", "recoil_comp_counts", "pitch_counts_end")})
        # Entry points need 2+ sightings per bin; insert one more and check they load.
        from bot.awareness import EntryPoints
        db["entry_events"].insert_many([{"map": MAP, "bearing": e["bearing"], "kind": e["kind"]}
                                        for e in db["entry_events"].find({"map": MAP, "kind": "first_seen"})])
        pts = EntryPoints(MAP, db).load()
        print(("PASS " if pts else "FAIL ") + f"entry points learned from sightings: {[(round(b), c) for b, c in pts]}")
        ok &= bool(pts)
    finally:
        db["entry_events"].delete_many({"map": MAP})
    # One frame of the compass HUD for a visual check
    import cv2
    from bot.awareness import Heading, ZombieMemory, EntryPoints, draw_compass
    h, m, e = Heading(0.6794), ZombieMemory(), EntryPoints(MAP)
    e.points = [(40.0, 5), (200.0, 3), (300.0, 2)]
    m.items = {1: {"bearing": 15.0, "h": 0.2, "first": 0, "last": 0}, 2: {"bearing": 170.0, "h": 0.5, "first": 0, "last": 0}}
    frame = cv2.resize(cv2.imread(str(ROOT / "docs" / "img" / "port_bot_kill.jpg")), (1280, 720))
    cv2.imwrite(str(ROOT / "local_data" / "awareness_frame.jpg"), draw_compass(frame, h, m, e, 2, True, 0))
    print("all checks passed" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
