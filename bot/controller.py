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
from collections import Counter, deque

import cv2
import numpy as np

from bot.awareness import DamageSensor, EntryPoints, Heading, RecoilTracker, ZombieMemory, draw_compass
from bot.scoring import classify_delta, in_head_zone, summarize as summarize_shots

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
        self.sent_x = self.sent_y = 0  # counts actually sent since the last frame (ego-motion for the tracker)

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
            self.sent_x += ix
            self.sent_y += iy
        return iy

    def take_sent(self):
        sx, sy = self.sent_x, self.sent_y
        self.sent_x = self.sent_y = 0
        return sx, sy

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
                save_frames_dir=None, log=print, abort_key="f8", cap=None, frame_every_s=1.0,
                map_name="kino", db=None, experiment_id=None, job_id=None):
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
    last_seen_t, last_seen_dir = -1e9, 1  # where the last target left the screen
    sweep_t0, sweep_dir = time.time(), 1
    pitch_cap = 280.0 * counts_per_px  # mouse counts for ~280 screen px of pitch either way
    shot_tally = Counter()      # hits and kill types estimated from points deltas (bot/scoring.py)
    head_fires = 0              # fire events with the crosshair on the target's head zone
    # --- awareness (bot/awareness.py): heading, zombie memory, damage, entry points, recoil ---
    heading = Heading(counts_per_px)
    memory = ZombieMemory()
    damage = DamageSensor()
    entries = EntryPoints(map_name, db)
    try:
        entries.load()
    except Exception as e:  # MongoDB unreachable: play without entry points
        log(f"[ctl] entry points not loaded ({type(e).__name__})")
    if entries.points:
        log(f"[ctl] entry points (bearing deg, sightings): {[(round(b), c) for b, c in entries.points]}")
    recoil = RecoilTracker(counts_per_px)
    turn_remaining = 0.0
    evade_until = 0.0
    unseen_hits = evade_turns = 0
    active = 0

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
                    # the death screen releases the mouse; a lost lock that can't be re-acquired = dead
                    game_over = True
                    log(f"[ctl] pointer lock gone at {el:.1f}s (death screen); ending episode")
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
                        shot_tally.update(classify_delta(p - points))
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

            sx, sy = keys.take_sent()
            heading.on_move(sx, sy)  # counts actually sent since the last frame
            obs = vp.step(S["det_conf"], S["target_rule"], S["aim_y_offset"], frame=frame,
                          ego=(-sx / counts_per_px, -sy / counts_per_px))
            n += 1
            det_sum += obs.telemetry["n_boxes"]
            tgt = obs.target
            if tgt is not None and (tgt.hits < 3 or tgt.missed > 2):
                tgt = None  # unconfirmed or long-gone: real zombies persist across frames, false positives flicker
            action = "SCAN"
            keys.tick(now)

            # awareness: count confirmed zombies (on screen or recently seen), learn entry points, react to hits
            confirmed = [tr for tr in obs.tracks if getattr(tr, "hits", 3) >= 3 and getattr(tr, "missed", 0) <= 2]
            visible_ids = {tr.id for tr in confirmed}
            active = memory.update(confirmed, heading, now)
            for tid in memory.new_ids:
                it = memory.items[tid]
                if it["h"] < 0.3:  # first seen far away = coming in through an entry point
                    entries.record(it["bearing"], "first_seen", el)
            if damage.update(frame, now) and not confirmed:
                # hit by something we cannot see: it is behind us. Turn around and back off.
                unseen_hits += 1
                evade_turns += 1
                entries.record(heading.yaw + 180.0, "unseen_hit", el)
                turn_remaining = 180.0 / heading.deg_per_count * (1 if evade_turns % 2 else -1)
                evade_until = now + 0.6
            if tgt is not None:
                recoil.observe(now, tgt.id, obs.err_y)

            if obs.nearest_h_frac > 0.6:
                close_calls += 1

            if tgt is not None:
                last_seen_t = now
                last_seen_dir = 1 if obs.err_x >= 0 else -1
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
                b = tgt.box
                sx, sy = 0.15 * b.w, 0.15 * b.h  # crosshair inside the (shrunk) body box = any shot hits
                on_body = (b.x1 + sx < W / 2 < b.x2 - sx) and (b.y1 + sy < H / 2 < b.y2 - sy)
                if err < S["fire_radius_px"] or on_body:
                    if acquiring and acquiring[1] >= 0:
                        lock_frame_counts.append(n - acquiring[1])
                        acquiring = (tgt.id, -1)  # count time-to-lock once per target
                    if ammo and ammo[0] == 0 and ammo[1] > 0:
                        if now - last_reload > 3:
                            keys.press("r")
                            last_reload = now
                    elif keys.fire(S["burst_ms"], now):
                        fires += 1
                        if in_head_zone(tgt.box, W / 2, H / 2):
                            head_fires += 1
                        recoil.on_fire(now, tgt.id, obs.err_y)
                        comp = recoil.comp_counts()
                        if comp:
                            pitch += keys.move(0, comp)  # pull down against the learned recoil kick
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
                step = max(S["aim_max_step"], 40)

                def clampc(c):
                    return max(-step, min(step, c))
                behind = memory.offscreen(now, visible_ids)
                watch = entries.next_watch(heading, now)
                if turn_remaining:  # hit from behind: turn around fast
                    d = max(-120.0, min(120.0, turn_remaining))
                    pitch += keys.move(d, level)
                    turn_remaining = 0.0 if abs(turn_remaining - d) < 1 else turn_remaining - d
                    action = "EVADE"
                elif now - last_seen_t < 1.5:
                    # just lost it: snap toward where it left the screen (it usually walked off the edge)
                    turn = last_seen_dir * max(2 * S["scan_turn_px"], 300)
                    pitch += keys.move(turn * dt * counts_per_px, level)
                elif behind:  # a zombie seen in the last 3 s is off screen: face the closest one
                    pitch += keys.move(clampc(heading.counts_to(behind[0]["bearing"])), level)
                    action = "TRACK"
                elif watch is not None:  # watch the learned entry points in turn
                    c = heading.counts_to(watch)
                    pitch += keys.move(clampc(c * 0.5) if abs(c) > 2 else 0, level)
                    action = "WATCH"
                else:
                    # sweep: 2 s one way, 1 s back -> re-checks the front arc while drifting a full circle
                    if now - sweep_t0 > (2.0 if sweep_dir > 0 else 1.0):
                        sweep_dir, sweep_t0 = -sweep_dir, now
                    turn = sweep_dir * S["scan_turn_px"]
                    pitch += keys.move(turn * dt * counts_per_px, level)
                if now < evade_until:
                    keys.hold("s", True)
                if (ammo and ammo[1] > 0 and now - last_reload > 3 and
                        (ammo[0] == 0 or (S["reload_when_clear_at"] and ammo[0] <= S["reload_when_clear_at"]))):
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
                img = vp.hud_frame(info={"action": action, "points": points if points is not None else "-",
                                         "strategy": f"gain {S['aim_gain']:.2f} fire<{S['fire_radius_px']}px "
                                                     f"{S['target_rule']}"},
                                   fire_radius_px=S["fire_radius_px"], retreat_prox=S["retreat_box_h"] / H)
                rec.write(draw_compass(img, heading, memory, entries, active, damage.recently_hit(now, 0.6), now))
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
        "hit_rate": round(hits_proxy / fires, 3) if fires else None,  # point-gain events per shot fired
        "close_calls": close_calls,
        "retreat_frames": retreat_frames,
        "time_no_target_pct": round(100.0 * no_target / n, 1) if n else 100.0,
        "det_count_avg": round(det_sum / n, 2) if n else 0.0,
        "stuck_events": stuck_events,
        "death_time_s": round(survival, 1) if game_over else None,
        "last_5s_summary": summary,
        "loop_fps": round(n / max(survival, 1e-6), 1),
        "pointer_lock_losses": lock_losses,
        **summarize_shots(shot_tally, head_fires, fires),
        "damage_events": damage.events,
        "unseen_hits": unseen_hits,
        "max_active_zombies": memory.max_active,
        "avg_active_zombies": round(memory.avg_active, 2),
        "entry_points_known": len(entries.points),
        "recoil_jump_px": round(recoil.jump_ema, 1) if recoil.jump_ema is not None else None,
        "recoil_comp_counts": recoil.comp_counts(),
        "pitch_counts_end": round(pitch, 1),
    }
    try:
        saved = entries.save(experiment_id, job_id)
        if saved:
            log(f"[ctl] {saved} entry sightings saved to MongoDB")
    except Exception as e:
        log(f"[ctl] entry sightings not saved ({type(e).__name__})")
    diagnostics = {k: v for k, v in diagnostics.items() if v is not None}
    return {"points": int(max(0, gained)), "survival_s": round(survival, 1), "game_over": game_over,
            "aborted": aborted["v"], "points_start": points0, "points_end": points, "diagnostics": diagnostics}
