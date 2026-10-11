"""Fast loop (plan §5.2): one episode on the browser port, executing a strategy's 12 params.

Units (agree with the strategy contract in TEAMMATE_HANDOFF.md):
- Frame coordinates are 1280x720 (capture is resized to this), so fire_radius_px / retreat_box_h are 720p pixels.
- aim_gain = fraction of the on-screen error corrected per frame. Mouse counts = err_px * aim_gain * COUNTS_PER_PX,
  where COUNTS_PER_PX is a per-machine calibration (config.yaml bot.counts_per_px).
- aim_max_step = max mouse counts per frame. scan_turn_px = screen px of turn per second when nothing is visible.
Returns points gained, survival seconds, and the §5.4 diagnostics.
"""
import math
import os
import time
from collections import deque

import cv2
import numpy as np

BASELINE = dict(det_conf=0.35, aim_gain=0.8, aim_max_step=80, aim_y_offset=0.15, fire_radius_px=25,
                burst_ms=120, retreat_box_h=300, strafe_mode="none", strafe_period_s=2.0,
                reload_when_clear_at=3, scan_turn_px=60, target_rule="center")

BOUNDS = {"det_conf": (0.15, 0.70), "aim_gain": (0.1, 2.0), "aim_max_step": (5, 200), "aim_y_offset": (0.0, 0.5),
          "fire_radius_px": (5, 80), "burst_ms": (30, 400), "retreat_box_h": (50, 500),
          "strafe_period_s": (0.5, 5.0), "reload_when_clear_at": (0, 8), "scan_turn_px": (0, 300)}


def clamp_params(p):
    """Defensive clamp; the strategy schema already validates, but never trust input that drives the mouse."""
    out = dict(BASELINE)
    out.update({k: v for k, v in p.items() if k in BASELINE})
    for k, (lo, hi) in BOUNDS.items():
        out[k] = type(BASELINE[k])(max(lo, min(hi, out[k])))
    if out["strafe_mode"] not in ("none", "left", "right", "alternate"):
        out["strafe_mode"] = "none"
    if out["target_rule"] not in ("nearest", "largest", "center"):
        out["target_rule"] = "center"
    return out


class Keys:
    """Held-key bookkeeping so every key is released on exit (abort, crash, or game over)."""

    def __init__(self):
        import pydirectinput
        pydirectinput.FAILSAFE = False
        pydirectinput.PAUSE = 0
        self.pdi = pydirectinput
        self.held = set()
        self.mouse_down_until = 0.0
        self.rx = self.ry = 0.0  # sub-count remainders, so slow scans and fine aim aren't truncated away

    def hold(self, key, on):
        if on and key not in self.held:
            self.pdi.keyDown(key)
            self.held.add(key)
        elif not on and key in self.held:
            self.pdi.keyUp(key)
            self.held.discard(key)

    def move(self, dx, dy):
        self.rx += dx
        self.ry += dy
        ix, iy = int(self.rx), int(self.ry)
        if ix or iy:
            self.pdi.moveRel(ix, iy, relative=True, disable_mouse_acceleration=True)
            self.rx -= ix
            self.ry -= iy
        return iy

    def fire(self, ms, now):
        if now >= self.mouse_down_until:
            self.pdi.mouseDown()
            self.mouse_down_until = now + ms / 1000.0
            return True
        return False

    def tick(self, now):
        if self.mouse_down_until and now >= self.mouse_down_until:
            self.pdi.mouseUp()
            self.mouse_down_until = 0.0

    def press(self, key):
        self.pdi.press(key)

    def release_all(self):
        for k in list(self.held):
            self.pdi.keyUp(k)
        self.held.clear()
        self.pdi.mouseUp()


def run_episode(game, vp, hud, params, max_s=180.0, counts_per_px=0.15, record_path=None,
                save_frames_dir=None, log=print, abort_key="f8", cap=None, frame_every_s=1.0):
    """Play one episode. game: bot.game.PortGame (playing, pointer locked). vp: VisionPipeline (live=False).
    hud: bot.hudread.HudReader. Returns dict(points, survival_s, diagnostics, ...)."""
    import keyboard
    from vision.capture import Capture
    from vision.run import Recorder

    S = clamp_params(params)
    keys = Keys()
    aborted = {"v": False}
    hk = keyboard.add_hotkey(abort_key, lambda: aborted.__setitem__("v", True))
    own_cap = cap is None
    if own_cap:
        cap = Capture(game.canvas_region(), 60)
    rec = Recorder(record_path, 1280, 720, 20) if record_path else None
    if save_frames_dir:
        os.makedirs(save_frames_dir, exist_ok=True)

    # --- telemetry accumulators ---
    n = aim_frames = flips = no_target = close_calls = retreat_frames = fires = 0
    det_sum = 0
    lock_frame_counts = []
    acquiring = None            # (target_id, frame index when acquired)
    prev_err_sign = None
    prev_tid = None
    hits_proxy = 0
    stuck_events = 0
    still_since = None
    prev_small = None
    last5 = deque()
    points0 = points = None
    ammo = None
    last_reload = 0.0
    lock_losses = 0
    game_over = False
    go_hits = 0
    pitch = 0.0  # dead-reckoned vertical mouse counts from the spawn view (+ = looking down)
    pitch_cap = 160.0 * counts_per_px  # mouse counts for ~160 screen px of pitch either way

    t0 = time.time()
    t_hud = t_frame_save = 0.0
    frame = None
    try:
        while True:
            now = time.time()
            el = now - t0
            if el >= max_s or aborted["v"]:
                break
            if n % 30 == 0 and not game.locked():  # paused / lost focus: release keys, re-acquire
                keys.release_all()
                lock_losses += 1
                if not game.ensure_locked():
                    log("[ctl] could not re-acquire pointer lock; ending episode")
                    break
            raw = cap.grab()
            frame = cv2.resize(raw, (1280, 720), interpolation=cv2.INTER_AREA)
            H, W = frame.shape[:2]

            # --- HUD: points every 1 s, ammo + game over every 2 s ---
            if now - t_hud > 1.0:
                p = hud.read_points(frame)
                if p is not None:
                    if points0 is None:
                        points0 = p
                    if points is not None and p > points:
                        hits_proxy += 1
                    points = p
                if int(el) % 2 == 0:
                    a = hud.read_ammo(frame)
                    ammo = a if a else ammo
                    go_hits = go_hits + 1 if (el > 5 and hud.game_over(frame)) else 0
                    if go_hits >= 2:  # two consecutive reads, 2 s apart
                        game_over = True
                        log(f"[ctl] game over at {el:.1f}s")
                        break
                t_hud = now

            obs = vp.step(S["det_conf"], S["target_rule"], S["aim_y_offset"], frame=frame)
            n += 1
            det_sum += obs.telemetry["n_boxes"]
            tgt = obs.target
            action = "SCAN"
            keys.tick(now)

            if obs.nearest_h_frac > 0.6:
                close_calls += 1

            if tgt is not None:
                aim_frames += 1
                err = math.hypot(obs.err_x, obs.err_y)
                # overshoot: sign flip of horizontal error on the SAME target (tracker keeps ids stable)
                sign = 1 if obs.err_x > 0 else -1
                if prev_tid == tgt.id and prev_err_sign is not None and sign != prev_err_sign and abs(obs.err_x) > 4:
                    flips += 1
                prev_err_sign, prev_tid = sign, tgt.id
                if acquiring is None or acquiring[0] != tgt.id:
                    acquiring = (tgt.id, n)
                k = S["aim_gain"] * counts_per_px
                step = S["aim_max_step"]
                mx = max(-step, min(step, obs.err_x * k))
                my = max(-step, min(step, obs.err_y * k))
                my = max(-pitch_cap - pitch, min(pitch_cap - pitch, my))  # never stare at the ceiling/floor
                pitch += keys.move(mx, my)
                if err < S["fire_radius_px"]:
                    if acquiring and acquiring[1] >= 0:
                        lock_frame_counts.append(n - acquiring[1])
                        acquiring = (tgt.id, -1)  # count time-to-lock once per target
                    if keys.fire(S["burst_ms"], now):
                        fires += 1
                    action = "FIRE"
                else:
                    action = "ENGAGE"
                retreat = tgt.box.h > S["retreat_box_h"]
                keys.hold("s", retreat)
                if retreat:
                    retreat_frames += 1
                    action = "RETREAT"
                mode = S["strafe_mode"]
                if mode == "alternate":
                    mode = "left" if int(el / S["strafe_period_s"]) % 2 == 0 else "right"
                keys.hold("a", mode == "left")
                keys.hold("d", mode == "right")
            else:
                no_target += 1
                prev_err_sign = prev_tid = None
                acquiring = None
                for k_ in ("s", "a", "d"):
                    keys.hold(k_, False)
                dt = 1 / max(vp.fps, 10)
                level = -0.25 * pitch if abs(pitch) > 2 else 0.0  # ease back to the horizon while scanning
                pitch += keys.move(S["scan_turn_px"] * dt * counts_per_px, level)
                if (S["reload_when_clear_at"] and ammo and ammo[0] < S["reload_when_clear_at"]
                        and ammo[1] > 0 and now - last_reload > 3):
                    keys.press("r")
                    last_reload = now
                    action = "RELOAD"

            # stuck: moving (strafe/backpedal held) but the image barely changes for > 1 s
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36)).astype(np.float32)
            moving = bool(keys.held)
            if prev_small is not None and moving and float(np.mean(np.abs(small - prev_small))) < 1.0:
                still_since = still_since or now
                if now - still_since > 1.0:
                    stuck_events += 1
                    still_since = None
            else:
                still_since = None
            prev_small = small

            last5.append((now, obs.telemetry["n_tracks"], obs.nearest_h_frac, action, ammo[0] if ammo else None))
            while last5 and now - last5[0][0] > 5:
                last5.popleft()

            if rec and n % 2 == 0:
                rec.write(vp.hud_frame(info={"action": action, "points": points if points is not None else "-",
                                             "strategy": f"gain {S['aim_gain']:.2f} fire<{S['fire_radius_px']}px "
                                                         f"{S['target_rule']}"},
                                       fire_radius_px=S["fire_radius_px"], retreat_prox=S["retreat_box_h"] / H))
            if save_frames_dir and now - t_frame_save > frame_every_s:
                cv2.imwrite(os.path.join(save_frames_dir, f"{int(now * 1000)}.jpg"), frame,
                            [cv2.IMWRITE_JPEG_QUALITY, 92])
                t_frame_save = now
    finally:
        keys.release_all()
        keyboard.remove_hotkey(hk)
        if own_cap:
            cap.close()
        if rec:
            rec.close()

    survival = min(max_s, time.time() - t0)
    gained = (points - points0) if (points is not None and points0 is not None) else 0
    cap_pts = 130 * fires  # +10 per hit, at most +130 for a kill; no shots means no points
    if gained > cap_pts:
        log(f"[ctl] OCR points gain {gained} exceeds plausible {cap_pts} for {fires} shots; capping")
        gained = cap_pts
    acts = [a[3] for a in last5]
    summary = (f"last 5s: avg {np.mean([a[1] for a in last5]) if last5 else 0:.1f} zombies visible, "
               f"nearest box {max([a[2] for a in last5], default=0):.2f} of screen height, "
               f"actions " + ", ".join(f"{a} {acts.count(a)}" for a in sorted(set(acts))) +
               (f", mag {last5[-1][4]}" if last5 and last5[-1][4] is not None else ""))
    diagnostics = {
        "overshoot_rate": round(flips / aim_frames, 3) if aim_frames else 0.0,
        "avg_frames_to_lock": round(float(np.mean(lock_frame_counts)), 1) if lock_frame_counts else None,
        "shots_without_target": 0,  # the controller only fires with a locked target
        "shots_fired": fires,
        "hits_proxy": hits_proxy,
        "close_calls": close_calls,
        "retreat_frames": retreat_frames,
        "time_no_target_pct": round(100.0 * no_target / n, 1) if n else 100.0,
        "det_count_avg": round(det_sum / n, 2) if n else 0.0,
        "stuck_events": stuck_events,
        "death_time_s": round(survival, 1) if game_over else None,
        "last_5s_summary": summary,
        "loop_fps": round(n / max(survival, 1e-6), 1),
        "pointer_lock_losses": lock_losses,
    }
    diagnostics = {k: v for k, v in diagnostics.items() if v is not None}
    return {"points": int(max(0, gained)), "survival_s": round(survival, 1), "game_over": game_over,
            "aborted": aborted["v"], "points_start": points0, "points_end": points, "diagnostics": diagnostics}
