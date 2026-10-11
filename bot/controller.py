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

from bot.awareness import EntryPoints, Heading, ZombieMemory, wrap180
from bot.scoring import classify_delta, in_head_zone, summarize as summarize_shots
from bot import livestream
from bot.tactics import Tactics

BASELINE = dict(det_conf=0.35, aim_gain=0.8, aim_max_step=80, aim_y_offset=0.15, fire_radius_px=25,
                burst_ms=120, retreat_box_h=300, strafe_mode="none", strafe_period_s=2.0,
                reload_when_clear_at=3, scan_turn_px=60, target_rule="center")

BOUNDS = {"det_conf": (0.15, 0.70), "aim_gain": (0.1, 2.0), "aim_max_step": (5, 200), "aim_y_offset": (0.0, 0.5),
          "fire_radius_px": (5, 80), "burst_ms": (30, 400), "retreat_box_h": (50, 500),
          "strafe_period_s": (0.5, 5.0), "reload_when_clear_at": (0, 8), "scan_turn_px": (0, 300)}


def damage_score(frame):
    """Red tint at the screen edges (BO1 shows blood/red when hit; the whole screen goes red near death).
    ~1 normally, ~33 when badly hurt. HUD corners with red art are excluded."""
    im = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA).astype(np.float32)
    red = np.clip(im[..., 2] - (im[..., 1] + im[..., 0]) / 2, 0, None)
    m = np.zeros(red.shape, bool)
    bw = 22
    m[:bw] = m[-bw:] = True
    m[:, :bw] = m[:, -bw:] = True
    m[135:, 200:] = False  # points/ammo HUD blood splat
    m[150:, :40] = False   # round tally
    return float(red[m].mean())


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

    def flush(self):
        pass  # OS input is sent immediately

    def release_all(self):
        for k in list(self.held):
            self.pdi.keyUp(k)
        self.held.clear()
        self.pdi.mouseUp()


def run_episode(game, vp, hud, params, max_s=180.0, counts_per_px=0.15, record_path=None,
                save_frames_dir=None, log=print, abort_key="f8", cap=None, frame_every_s=1.0, latency_s=0.11,
                publisher=None, job_id=None, on_progress=None, map_name="kino", db=None, experiment_id=None,
                live_view=False):
    """Play one episode. game: bot.game.PortGame (playing, pointer locked). vp: VisionPipeline (live=False).
    hud: bot.hudread.HudReader. Returns dict(points, survival_s, diagnostics, ...)."""
    import keyboard
    from vision.capture import Capture
    from vision.run import Recorder

    S = clamp_params(params)
    keys = game.keys() if hasattr(game, "keys") else Keys()
    # Moves reach the screen ~latency_s later (input -> game render -> capture). Without accounting for that,
    # several corrections stack up before the first one is visible: overshoot, oscillation, lost track ids.
    inflight = deque()  # (t_sent, counts_x, counts_y)
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
    first_seen_s = None
    # damage reaction
    dmg_base = 1.0                   # running baseline of the red score
    dmg_frames = dmg_events = evades = 0
    hurt_until = 0.0                 # react while recently hit
    evade_left = 0.0                 # mouse counts of emergency turn still to do
    evade_dir = 1
    react_times = []                 # seconds from hit (no target) to having a target again
    hit_unseen_t = None
    yaw180 = 2560 * counts_per_px    # ~1280 px is ~90 deg of yaw, so 180 deg ~ 2560 px
    next_drift = time.time() + 3.0
    last_visible_t = -1e9  # any confirmed zombie on screen (not just the target)
    sweep_t0, sweep_dir = time.time(), 1
    pitch_cap = 280.0 * counts_per_px  # mouse counts for ~280 screen px of pitch either way
    tactics = Tactics()  # rule-based target choice + movement (bot/tactics.py)
    shot_tally = Counter()  # hits and kill types from points deltas (bot/scoring.py)
    head_fires = 0
    heading, memory = Heading(counts_per_px), ZombieMemory()  # for the 360 radar
    entries = EntryPoints(map_name, db)
    try:
        entries.load()
    except Exception:
        pass
    active = 0
    t_progress = 0.0

    def radar_now():
        return {"active": active,
                "zombies": [{"rel_deg": wrap180(v["bearing"] - heading.yaw), "h_frac": v["h"],
                             "visible": now - v["last"] < 0.2, "target": False} for v in memory.items.values()],
                "entries": [wrap180(b - heading.yaw) for b, _ in entries.points]}

    t0 = time.time()
    t_hud = t_frame_save = t_pub = 0.0
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
                        tactics.on_points(p)
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

            landed_x = landed_y = 0
            while inflight and now - inflight[0][0] >= latency_s:
                _, cx_, cy_ = inflight.popleft()
                landed_x += cx_
                landed_y += cy_
            pend_x = sum(m[1] for m in inflight) / counts_per_px  # screen px still to arrive
            pend_y = sum(m[2] for m in inflight) / counts_per_px
            obs = vp.step(S["det_conf"], S["target_rule"], S["aim_y_offset"], frame=frame,
                          ego=(-landed_x / counts_per_px, -landed_y / counts_per_px))
            n += 1
            det_sum += obs.telemetry["n_boxes"]
            tgt = obs.target
            if tgt is not None and (tgt.hits < 3 or tgt.missed > 2):
                tgt = None  # unconfirmed or long-gone: real zombies persist across frames, false positives flicker
            tac = tactics.update(obs, now, W, H, S, points)
            if tac.target is not None:  # highest threat across all confirmed zombies, chest aim when close
                tgt = tac.target
                obs.err_x, obs.err_y = tac.err_x, tac.err_y
            confirmed = [t for t in obs.tracks if t.hits >= 3 and t.missed <= 2]
            active = memory.update(confirmed, heading, now)
            for tid_ in memory.new_ids:
                if memory.items[tid_]["h"] < 0.3:  # first seen far away = an entry point
                    entries.record(memory.items[tid_]["bearing"], "first_seen", el)
            action = "SCAN"
            keys.tick(now)

            dscore = damage_score(frame)
            hit = dscore > max(6.0, 3.0 * dmg_base)
            if not hit:
                dmg_base = 0.98 * dmg_base + 0.02 * dscore
            if hit:
                dmg_frames += 1
                if now > hurt_until:  # new hit event
                    dmg_events += 1
                    if tgt is None:
                        evades += 1
                        evade_left = yaw180
                        # a zombie seen recently on one side is the likely attacker; else turn around
                        evade_dir = last_seen_dir if now - last_seen_t < 3.0 else 1
                        hit_unseen_t = now
                hurt_until = now + 1.2
            if tgt is not None and hit_unseen_t is not None:
                react_times.append(now - hit_unseen_t)
                hit_unseen_t = None
            if obs.nearest_h_frac > 0.6:
                close_calls += 1
            if any(t.hits >= 3 and t.missed == 0 for t in obs.tracks):
                last_visible_t = now
                if first_seen_s is None:
                    first_seen_s = round(el, 1)

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
                # error the camera will still have once in-flight moves land
                ex, ey = obs.err_x - pend_x, obs.err_y - pend_y
                k = S["aim_gain"] * counts_per_px
                step = S["aim_max_step"]
                mx = max(-step, min(step, ex * k))
                my = max(-step, min(step, ey * k))
                my = max(-pitch_cap - pitch, min(pitch_cap - pitch, my))  # never stare at the ceiling/floor
                pitch += keys.move(mx, my)
                b = tgt.box
                sx, sy = 0.15 * b.w, 0.15 * b.h  # crosshair inside the (shrunk) body box = any shot hits
                on_body = (b.x1 + sx < W / 2 < b.x2 - sx) and (b.y1 + sy < H / 2 < b.y2 - sy)
                if math.hypot(ex, ey) < S["fire_radius_px"] or on_body:
                    if acquiring and acquiring[1] >= 0:
                        lock_frame_counts.append(n - acquiring[1])
                        acquiring = (tgt.id, -1)  # count time-to-lock once per target
                    if ammo and ammo[0] == 0 and ammo[1] > 0:
                        if now - last_reload > 3:
                            keys.press("r")
                            last_reload = now
                    elif keys.fire(S["burst_ms"], now):
                        fires += 1
                        tactics.on_fire(now, points)
                        if in_head_zone(tgt.box, W / 2, H / 2):
                            head_fires += 1
                    action = "FIRE"
                else:
                    action = "ENGAGE"
                evade_left = 0.0  # a target is in view: stop any emergency turn
                hurt = now < hurt_until
                retreat = tgt.box.h > S["retreat_box_h"] or hurt
                keys.hold("w", False)
                keys.hold("s", retreat)
                if retreat:
                    retreat_frames += 1
                    action = "RETREAT"
                mode = S["strafe_mode"]
                if hurt and mode == "none":
                    mode = "alternate"  # being hit while fighting: don't stand still
                if mode == "alternate":
                    mode = "left" if int(el / S["strafe_period_s"]) % 2 == 0 else "right"
                keys.hold("a", mode == "left")
                keys.hold("d", mode == "right")
                if tac.label:  # tactics override: REPOSITION (blocked shot), KITE (close), SWARMED (3+ close)
                    for k_ in ("w", "s", "a", "d"):
                        keys.hold(k_, tac.keys[k_])
                    if tac.keys["s"] and not retreat:
                        retreat_frames += 1
                    action = tac.label
            else:
                no_target += 1
                prev_err_sign = prev_tid = None
                acquiring = None
                keys.hold("s", False)
                drifting = next_drift < now < next_drift + 0.4
                drift_key = "a" if int(next_drift) % 2 else "d"
                keys.hold("a", drifting and drift_key == "a")
                keys.hold("d", drifting and drift_key == "d")
                if now >= next_drift + 0.4:
                    next_drift = now + 3.0  # idle drift: a 0.4 s side-step every 3 s so the bot doesn't plant
                if evade_left > 0:  # hit by something we can't see: whip around and move away from it
                    stepc = min(250.0, evade_left)
                    keys.move(evade_dir * stepc, 0)
                    evade_left -= stepc
                    keys.hold("w", True)
                    action = "EVADE"
                else:
                    keys.hold("w", now < hurt_until)  # keep moving while still being hit
                dt = 1 / max(vp.fps, 10)
                level = -0.25 * pitch if abs(pitch) > 2 else 0.0  # ease back to the horizon while scanning
                if action == "EVADE":
                    turn = 0.0  # the emergency turn above already moved the camera
                elif now - last_seen_t < 1.5:
                    # just lost it: snap toward where it left the screen (it usually walked off the edge)
                    turn = last_seen_dir * max(2 * S["scan_turn_px"], 300)
                else:
                    # sweep: 2 s one way, 1 s back -> re-checks the front arc while drifting a full circle
                    if now - sweep_t0 > (2.0 if sweep_dir > 0 else 1.0):
                        sweep_dir, sweep_t0 = -sweep_dir, now
                    turn = sweep_dir * S["scan_turn_px"]
                pitch += keys.move(turn * dt * counts_per_px, level)
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

            sx, sy = keys.take_sent()
            heading.on_move(sx, sy)  # dead-reckoned yaw for the radar
            if sx or sy:
                inflight.append((now, sx, sy))
            keys.flush()
            last5.append((now, obs.telemetry["n_tracks"], obs.nearest_h_frac, action, ammo[0] if ammo else None))
            while last5 and now - last5[0][0] > 5:
                last5.popleft()

            if on_progress and now - t_progress >= 2.0:  # dashboard heartbeat for the MongoDB job mode (Jeffrey)
                t_progress = now
                try:
                    on_progress(frame=vp.hud_frame(info={"action": action}), fps=round(vp.fps, 1),
                                detections=obs.telemetry["n_tracks"], action=action, active_zombies=active,
                                points=(points - points0) if (points is not None and points0 is not None) else 0,
                                elapsed_s=round(el, 1))
                except Exception:
                    pass  # observability is best effort
            if publisher is not None and now - t_pub > 2.0:  # live "Worker viewport" on the team dashboard
                t_pub = now
                publisher.heartbeat(job_id, vp.hud_frame(info={"action": action}), fps=round(vp.fps, 1),
                                    detections=obs.telemetry["n_tracks"], action=action,
                                    points=(points - points0) if (points is not None and points0 is not None) else 0,
                                    elapsed_s=round(el, 1), force=True)
            if (rec or live_view or livestream.active()) and n % 2 == 0:
                img = vp.hud_frame(radar=radar_now(), info={"action": action, "points": points if points is not None else "-",
                                             "strategy": f"gain {S['aim_gain']:.2f} fire<{S['fire_radius_px']}px "
                                                         f"{S['target_rule']}"},
                                       fire_radius_px=S["fire_radius_px"], retreat_prox=S["retreat_box_h"] / H)
                if rec:
                    rec.write(img)
                livestream.publish(img)  # dashboard live view
                if live_view:
                    cv2.imshow("ZOMBIE VISION (live)", img)
                    cv2.waitKey(1)
            if save_frames_dir and now - t_frame_save > frame_every_s:
                cv2.imwrite(os.path.join(save_frames_dir, f"{int(now * 1000)}.jpg"), frame,
                            [cv2.IMWRITE_JPEG_QUALITY, 92])
                t_frame_save = now
    finally:
        keys.release_all()
        keyboard.remove_hotkey(hk)
        if own_cap and not game_over:
            cap.close()
        if rec:
            rec.close()

    survival = min(max_s, time.time() - t0)
    t_end = time.time()
    board = None
    if game_over:  # exact numbers from the post-game table (appears a few seconds after GAME OVER)
        t_sb = time.time()
        while time.time() - t_sb < 15 and board is None:
            try:
                board = hud.scoreboard(cv2.resize(cap.grab(), (1280, 720), interpolation=cv2.INTER_AREA))
            except Exception:
                board = None
            if board is None:
                time.sleep(0.7)
        if board:
            log(f"[ctl] scoreboard: {board}")
        if own_cap:
            cap.close()
    gained = (points - points0) if (points is not None and points0 is not None) else 0
    if board:  # trust the scoreboard over live HUD OCR (Kino starts with 500)
        gained = max(0, board["points"] - (points0 if points0 is not None else 500))
    cap_pts = 130 * fires  # +10 per hit, at most +130 for a kill; no shots means no points
    if gained > cap_pts and not board:
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
        "kills": board["kills"] if board else None,
        "headshots": board["headshots"] if board else None,
        "first_zombie_seen_s": first_seen_s,
        # died with no zombie on screen for the last 2 s = attacked from behind / off-screen
        "death_unseen_attacker": (t_end - last_visible_t > 2.0) if game_over else None,
        "s_since_last_zombie_seen_at_death": round(min(t_end - last_visible_t, 999), 1) if game_over else None,
        "last_5s_summary": summary,
        "damage_frames": dmg_frames,
        "damage_events": dmg_events,
        **{f"tac_{k}": v for k, v in tactics.stats.items()},
        **summarize_shots(shot_tally, head_fires, fires),
        "max_active_zombies": memory.max_active,
        "entry_points_known": len(entries.points),
        "evades_from_unseen_hits": evades,
        "avg_s_to_face_attacker": round(float(np.mean(react_times)), 2) if react_times else None,
        "loop_fps": round(n / max(survival, 1e-6), 1),
        "pointer_lock_losses": lock_losses,
    }
    diagnostics = {k: v for k, v in diagnostics.items() if v is not None}
    try:
        entries.save(experiment_id, job_id)
    except Exception:
        pass
    return {"points": int(max(0, gained)), "survival_s": round(survival, 1), "game_over": game_over,
            "aborted": aborted["v"], "points_start": points0, "points_end": points, "diagnostics": diagnostics}
