"""Drive the browser port in an isolated Chrome (own profile, CDP input, no physical mouse) and screenshot it.

python -m tools.port_probe --map kino --out D:/czb/port/kino
Logs each loading stage, clicks Play / "Click to start", then screenshots gameplay every second.
"""
import argparse
import json
import os
import time

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

from common.paths import data_path
from vision.capture import find_window_region

user32 = __import__("ctypes").windll.user32


def focus_window(title_substr):
    """Pointer lock needs the browser window in the OS foreground. Alt tap lets SetForegroundWindow succeed."""
    import ctypes
    import ctypes.wintypes as wt
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if user32.IsWindowVisible(hwnd) and title_substr.lower() in buf.value.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        return False
    if user32.GetForegroundWindow() == found[0]:
        return True  # already in front; an Alt tap here would make Chrome drop pointer lock (game pauses)
    user32.keybd_event(0x12, 0, 0, 0)  # Alt tap lets SetForegroundWindow succeed from a background process
    user32.keybd_event(0x12, 0, 2, 0)
    user32.ShowWindow(found[0], 9)
    return bool(user32.SetForegroundWindow(found[0]))


def canvas_region(title):
    """Physical-pixel screen rect of the 16:9 viewport at the bottom of the browser client area."""
    import ctypes
    r = find_window_region(title)
    if r is None:
        return None
    L, T, R, B = r
    w = R - L
    vw, vh = w - (w % 16), (w - (w % 16)) * 9 // 16
    return (L + (w - vw) // 2, B - vh, L + (w - vw) // 2 + vw, B)


def shift_px(a, b):
    """Horizontal/vertical image shift between two screenshots (phase correlation on the center crop)."""
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
    (dx, dy), resp = cv2.phaseCorrelate(ga, gb)
    diff = float(np.mean(np.abs(ga - gb)))
    return dx, dy, resp, diff


def grab(page):
    return cv2.imdecode(np.frombuffer(page.screenshot(), np.uint8), cv2.IMREAD_COLOR)

URL = "https://vel.gg/bo1z/{}"


def real_click_canvas(page, say):
    """Physical click on the canvas center (SendInput). Pointer lock wants a real, focused user gesture."""
    import pydirectinput
    pydirectinput.FAILSAFE = False
    g = page.evaluate("""() => { const r = document.getElementById('game').getBoundingClientRect();
        return {sx: screenX, sy: screenY, ow: outerWidth, oh: outerHeight, iw: innerWidth, ih: innerHeight,
                dpr: devicePixelRatio, cx: r.left + r.width / 2, cy: r.top + r.height / 2}; }""")
    border = (g["ow"] - g["iw"]) / 2
    x = (g["sx"] + border + g["cx"]) * g["dpr"]
    y = (g["sy"] + g["oh"] - g["ih"] - border + g["cy"]) * g["dpr"]
    say("real click at", int(x), int(y), g)
    pydirectinput.click(int(x), int(y))


def gate1(page, say, title, counts=80, reps=3):
    """Measure camera yaw response to mouse input. Run before zombies arrive (camera otherwise static)."""
    import pydirectinput
    pydirectinput.FAILSAFE = False
    say("GATE1 pointer locked:", page.evaluate("() => !!document.pointerLockElement"))
    res = {"noise": [], "cdp": [], "sendinput": []}
    for r in range(reps):
        a = grab(page); time.sleep(0.3); b = grab(page)
        res["noise"].append(shift_px(a, b))
        # CDP relative move (no physical mouse)
        a = grab(page)
        page.mouse.move(640 + counts, 360, steps=4); time.sleep(0.4)
        b = grab(page)
        res["cdp"].append(shift_px(a, b))
        page.mouse.move(640, 360, steps=4); time.sleep(0.4)
        # SendInput relative move (what controller.py uses)
        focus_window(title)
        a = grab(page)
        for _ in range(4):
            pydirectinput.moveRel(counts // 4, 0, relative=True, disable_mouse_acceleration=True); time.sleep(0.02)
        time.sleep(0.4)
        b = grab(page)
        res["sendinput"].append(shift_px(a, b))
        if r == 0:
            cv2.imwrite(data_path("port", "gate1_before.png"), a)
            cv2.imwrite(data_path("port", "gate1_after.png"), b)
        for _ in range(4):
            pydirectinput.moveRel(-counts // 4, 0, relative=True, disable_mouse_acceleration=True); time.sleep(0.02)
        time.sleep(0.4)
    for k, v in res.items():
        say(f"GATE1 {k:9s} " + "  ".join("dx=%6.1f dy=%5.1f resp=%.2f diff=%4.1f" % x for x in v))
    return res


def bot_smoke(page, say, title, seconds, px_per_count, out):
    """Minimal closed loop on the real port: screenshot -> TensorRT detector -> aim/scan/fire -> HUD recording."""
    import pydirectinput
    from vision.pipeline import VisionPipeline
    from vision.run import Recorder
    pydirectinput.FAILSAFE = False
    from vision.capture import Capture
    import yaml
    from common.paths import resolve_weights
    weights = resolve_weights(yaml.safe_load(open("config.yaml"))["vision"]["weights"])
    print("detector weights:", weights)
    vp = VisionPipeline.from_config("config.yaml", live=False, weights=weights)
    region = canvas_region(title)
    say("BOT canvas region", region)
    cap = Capture(region, 60)
    rec = Recorder(os.path.join(out, "bot_smoke_hud.mp4"), 1280, 720, 20)
    if px_per_count <= 0.05 and os.path.exists("calibration.json"):  # Gate 1 estimate failed: use calibration
        px_per_count = 1.0 / json.load(open("calibration.json"))["counts_per_px"]
        say("BOT using calibration.json: px per count", round(px_per_count, 3))
    gain = 0.6 / px_per_count if px_per_count > 0.05 else 0.15  # counts per px of error, damped
    focus_window(title)
    t0 = time.time()
    n = locks = shots = 0
    recovers = 0
    while time.time() - t0 < seconds:
        if n % 20 == 0 and not page.evaluate("() => !!document.pointerLockElement"):
            recovers += 1
            say("BOT pointer lock lost -> resume")
            focus_window(title)
            pydirectinput.press("esc")
            time.sleep(0.4)
            real_click_canvas(page, say)
            time.sleep(0.5)
        frame = cv2.resize(cap.grab(), (1280, 720))
        obs = vp.step(conf=0.3, rule="nearest", aim_y_offset=0.25, frame=frame)
        action = "SCAN"
        if obs.target is not None:
            locks += 1
            action = "ENGAGE"
            dx = int(max(-150, min(150, obs.err_x * gain)))
            dy = int(max(-80, min(80, obs.err_y * gain)))
            if dx or dy:
                pydirectinput.moveRel(dx, dy, relative=True, disable_mouse_acceleration=True)
            if abs(obs.err_x) < 40 and abs(obs.err_y) < 60:
                action = "FIRE"
                pydirectinput.mouseDown(); time.sleep(0.08); pydirectinput.mouseUp()
                shots += 1
        else:
            if n % 4 == 0:
                pydirectinput.moveRel(12, 0, relative=True, disable_mouse_acceleration=True)
        if n % 3 == 0:
            rec.write(vp.hud_frame(info={"action": action}))
        n += 1
    rec.close()
    cap.close()
    cv2.imwrite(os.path.join(out, "bot_last_raw.png"), frame)
    say(f"BOT frames={n} ({n / seconds:.1f}/s) frames_with_lock={locks} shots={shots} recovers={recovers}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="kino")
    ap.add_argument("--out", default=None)
    ap.add_argument("--load-timeout", type=float, default=600)
    ap.add_argument("--play-seconds", type=float, default=60)
    ap.add_argument("--profile", default=data_path("chrome-profile", ""))
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--gate1", action="store_true", help="test whether mouse input turns the camera")
    ap.add_argument("--title", default="Kino der Toten")
    ap.add_argument("--bot-seconds", type=float, default=0)
    args = ap.parse_args()
    out = args.out or data_path("port", args.map, "")
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, "probe.log"), "w", encoding="utf-8")

    def say(*a):
        s = f"[{time.strftime('%H:%M:%S')}] " + " ".join(str(x) for x in a)
        print(s, flush=True)
        log.write(s + "\n")
        log.flush()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, channel="chrome", headless=False, viewport={"width": 1280, "height": 720},
            args=["--window-size=1296,860", "--ignore-gpu-blocklist", "--force_high_performance_gpu", "--enable-gpu-rasterization",
                  "--enable-unsafe-webgpu", "--hide-crash-restore-bubble", "--no-first-run", "--disable-background-timer-throttling",
                  "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("console", lambda m: say("console", m.type, m.text[:200]) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: say("pageerror", str(e)[:300]))
        t0 = time.time()
        page.goto(URL.format(args.map), wait_until="domcontentloaded")
        gpu = page.evaluate("""() => {
            const c = document.createElement('canvas'); const gl = c.getContext('webgl2');
            const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
            return {webgl2: !!gl, renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
                    webgpu: !!navigator.gpu, isolated: self.crossOriginIsolated, threads: navigator.hardwareConcurrency};
        }""")
        say("gpu", json.dumps(gpu))

        last = None
        clicked_texts = set()
        clicked_start = False
        t_ready = None
        shot_i = 0
        while time.time() - t0 < args.load_timeout:
            st = page.evaluate("""() => ({
                screen: document.body.dataset.screen,
                status: document.getElementById('status')?.textContent?.trim(),
                play: document.getElementById('play')?.textContent?.trim(),
                playDisabled: document.getElementById('play')?.disabled,
                playHidden: document.getElementById('play')?.hidden,
                transfer: document.getElementById('transfer-details')?.textContent?.trim(),
                load: document.getElementById('load-details')?.textContent?.trim(),
                err: document.getElementById('error-message')?.hidden === false ? document.getElementById('error-message').textContent.trim() : null,
                overlay: getComputedStyle(document.getElementById('overlay')).display,
            })""")
            key = json.dumps(st)
            if key != last:
                say(f"t+{time.time() - t0:5.1f}s", key)
                last = key
            if st["err"]:
                say("ERROR", st["err"])
                break
            btn_live = st["play"] and not st["playDisabled"] and not st["playHidden"] and st["overlay"] != "none"
            if btn_live and st["play"] not in clicked_texts:
                say("clicking button:", st["play"])
                page.click("#play")
                clicked_texts.add(st["play"])
                if st["play"].lower().startswith("click to start"):
                    t_ready = time.time()
            # the engine draws its own intro ("CLICK TO START" on the canvas) after the overlay closes
            if not clicked_start and (st["overlay"] == "none" or (t_ready and time.time() - t_ready > 3)):
                page.screenshot(path=os.path.join(out, "ready.png"))
                say("focus window:", focus_window(args.title), "| region:", find_window_region(args.title))
                time.sleep(0.5)
                say("clicking canvas (real input) to start")
                real_click_canvas(page, say)
                time.sleep(1.0)
                page.keyboard.press("Enter")
                clicked_start = True
                t_play = time.time()
            if clicked_start:
                if time.time() - t_play > args.play_seconds:
                    break
                if shot_i in (5, 15):  # nudge again in case the intro wanted another click
                    if not page.evaluate("() => !!document.pointerLockElement"):
                        focus_window(args.title)
                        time.sleep(0.3)
                        real_click_canvas(page, say)
                if args.gate1 and shot_i == 3:
                    res = gate1(page, say, args.title)
                    good = [abs(x[0]) for x in res["sendinput"] if x[2] > 0.1]
                    ppc = (sum(good) / len(good) / 80) if good else 0.0
                    say(f"GATE1 screen px per mouse count ~ {ppc:.2f}")
                    if args.bot_seconds:
                        bot_smoke(page, say, args.title, args.bot_seconds, ppc, out)
                        break
                page.screenshot(path=os.path.join(out, f"play_{shot_i:03d}.png"))
                shot_i += 1
                time.sleep(1.0)
                continue
            if int(time.time() - t0) % 15 == 0:
                page.screenshot(path=os.path.join(out, f"load_{int(time.time() - t0):04d}.png"))
            time.sleep(0.5)
        page.screenshot(path=os.path.join(out, "final.png"))
        page.keyboard.press("Escape")  # release pointer lock before closing
        say("done", f"elapsed {time.time() - t0:.0f}s, gameplay shots {shot_i}")
        if args.keep_open:
            input("enter to close")
        ctx.close()


if __name__ == "__main__":
    main()
