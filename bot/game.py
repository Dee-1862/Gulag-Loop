"""The browser port as an environment: isolated Chrome on the dGPU, start, restart, keep pointer lock.

Lessons baked in (see README "Browser port test"):
- Chrome defaults to the integrated GPU -> --force_high_performance_gpu.
- Pointer lock needs a REAL click on a FOREGROUND window; CDP clicks are refused.
- An Alt tap while in game makes Chrome drop pointer lock (game pauses), so only refocus when needed.
"""
import ctypes
import os
import ctypes.wintypes as wt
import time

from vision.capture import find_window_region  # also makes this process DPI-aware

user32 = ctypes.windll.user32
URL = "https://vel.gg/bo1z/{}"
CHROME_ARGS = ["--window-size=1296,860", "--ignore-gpu-blocklist", "--force_high_performance_gpu",
               "--enable-gpu-rasterization", "--enable-unsafe-webgpu", "--hide-crash-restore-bubble",
               "--no-first-run", "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
               "--disable-backgrounding-occluded-windows"]


def find_hwnd(title_substr):
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
    return found[0] if found else None


def focus_window(title_substr):
    hwnd = find_hwnd(title_substr)
    if hwnd is None:
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    user32.keybd_event(0x12, 0, 0, 0)  # Alt tap lets a background process take the foreground
    user32.keybd_event(0x12, 0, 2, 0)
    user32.ShowWindow(hwnd, 9)
    return bool(user32.SetForegroundWindow(hwnd))


def is_foreground(title_substr):
    hwnd = find_hwnd(title_substr)
    return hwnd is not None and user32.GetForegroundWindow() == hwnd


# 2x2 tiles of 640x360 CSS viewports in --app windows (no tab strip / omnibox), DIP coordinates
TILE_W, TILE_H, APP_CHROME_H = 640, 360, 150  # --app window: title bar + frame measured at 150 DIP


class PortGame:
    def __init__(self, map_id="kino", profile=None, log=print, slot=None, input_mode="os"):
        """slot=None: one big window driven by real OS input (pydirectinput, needs the foreground).
        slot=0..3 + input_mode='js': small tiled window driven by in-page synthetic input (bot.jsinput),
        so several games run side by side without focus or the physical mouse."""
        self.map_id = map_id
        self.slot = slot
        self.input_mode = input_mode
        # Headless (no window at all): only with in-page input + screencast, i.e. a slot. Watch it in the dashboard.
        self.headless = slot is not None and bool(os.environ.get("BOT_HEADLESS"))
        from bot.paths import p as _p
        profile = profile or _p("chrome-profile")
        self.profile = profile if slot is None else f"{profile}-{slot}"
        self.log = log
        self._pw = self._ctx = self.page = None
        self.title = None
        self._region = None

    # ---------- lifecycle ----------
    def open(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        if self.slot is None:
            args, viewport = CHROME_ARGS, {"width": 1280, "height": 720}
        else:
            col, row = self.slot % 2, self.slot // 2
            args = [a for a in CHROME_ARGS if not a.startswith("--window-size")] + [
                "--app=about:blank", f"--window-size={TILE_W + 16},{TILE_H + APP_CHROME_H}",
                (f"--window-position={-4000 - col * (TILE_W + 24)},{row * 300}" if os.environ.get("BOT_OFFSCREEN")
                 else f"--window-position={col * (TILE_W + 24)},{row * 300}"),  # may overlap: frames come from the page
                "--disable-features=CalculateNativeWinOcclusion",  # keep rendering when hidden/off-screen
                "--autoplay-policy=no-user-gesture-required"]
            viewport = None
        kw = {"viewport": viewport} if viewport else {"no_viewport": True}  # no_viewport: window size is respected
        if self.headless:  # no window to size: give the page a real 720p viewport to render into
            kw = {"viewport": {"width": 1280, "height": 720}}
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.profile, channel="chrome", headless=self.headless,
            args=args + (["--use-angle=d3d11", "--enable-gpu"] if self.headless else []),
            ignore_default_args=["--enable-automation"], **kw)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        if self.input_mode == "js":
            from bot.jsinput import INIT_SCRIPT
            self.page.add_init_script(INIT_SCRIPT)
        return self

    def close(self):
        try:
            if self.page:
                self.page.keyboard.press("Escape")
        except Exception:
            pass
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()

    def gpu(self):
        return self.page.evaluate("""() => { const gl = document.createElement('canvas').getContext('webgl2');
            const d = gl && gl.getExtension('WEBGL_debug_renderer_info');
            return d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : null; }""")

    def start(self, timeout=300):
        """Load the map and get into gameplay with pointer lock. Returns seconds taken."""
        t0 = time.time()
        if self.page.url.startswith("http"):
            self.page.reload(wait_until="domcontentloaded")
        else:
            self.page.goto(URL.format(self.map_id), wait_until="domcontentloaded")
        self.title = self.page.title()
        if self.slot is not None:  # unique window title so each worker finds its own tile
            self.title = f"bot{self.slot} {self.map_id}"
            self.page.evaluate("t => { document.title = t; new MutationObserver(() => { if (document.title !== t) "
                               "document.title = t; }).observe(document.querySelector('title'), {childList: true}); }",
                               self.title)
        clicked = set()
        t_ready = None
        while time.time() - t0 < timeout:
            st = self.page.evaluate("""() => { const b = document.getElementById('play');
                const e = document.getElementById('error-message');
                return {screen: document.body.dataset.screen, play: b?.textContent?.trim(),
                        live: !!b && !b.disabled && !b.hidden,
                        overlay: getComputedStyle(document.getElementById('overlay')).display,
                        err: e && !e.hidden ? e.textContent.trim() : null}; }""")
            if st["err"]:
                raise RuntimeError(f"port error: {st['err']}")
            if st["live"] and st["overlay"] != "none" and st["play"] not in clicked:
                self.page.click("#play")
                clicked.add(st["play"])
                if st["play"].lower().startswith("click to start"):
                    t_ready = time.time()
            if self.input_mode == "js":
                if st["screen"] == "playing" and self.locked():
                    time.sleep(1.0)
                    self.log(f"[game] slot {self.slot} {self.map_id} playing after {time.time() - t0:.0f}s")
                    return time.time() - t0
                if t_ready and time.time() - t_ready > 2 and int(time.time() * 2) % 4 == 0:
                    self.ensure_locked(tries=1)  # clicks the canvas: dismisses the intro, takes the (fake) lock
            elif st["screen"] == "playing" or (t_ready and time.time() - t_ready > 2):
                if self.ensure_locked(tries=4):
                    self.log(f"[game] {self.map_id} playing after {time.time() - t0:.0f}s")
                    return time.time() - t0
            time.sleep(0.5)
        raise TimeoutError("game did not start")

    # ---------- input focus ----------
    def locked(self):
        try:
            return bool(self.page.evaluate("() => !!document.pointerLockElement"))
        except Exception:
            return False

    def keys(self):
        """Input backend for the controller."""
        if self.input_mode == "js":
            from bot.jsinput import JSKeys
            return JSKeys(self.page)
        from bot.controller import Keys
        return Keys()

    def real_click_canvas(self):
        import pydirectinput
        pydirectinput.FAILSAFE = False
        r = self.canvas_region()
        pydirectinput.click((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)

    def ensure_locked(self, tries=3):
        if self.input_mode == "js":
            # fake lock: a click on the canvas lets the page request it (the "click to start" path)
            for _ in range(tries):
                if self.locked():
                    return True
                box = self.page.locator("#game").bounding_box()
                if box:
                    self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                time.sleep(0.8)
                if not self.locked():
                    self.page.evaluate("() => window.__bot.lock()")
                    time.sleep(0.5)
            return self.locked()
        for _ in range(tries):
            if self.locked():
                return True
            focus_window(self.title)
            time.sleep(0.3)
            self.real_click_canvas()
            time.sleep(0.8)
            if not self.locked():  # maybe the game paused itself; Esc resumes from the pause menu
                import pydirectinput
                pydirectinput.press("esc")
                time.sleep(0.4)
                self.real_click_canvas()
                time.sleep(0.8)
        return self.locked()

    def canvas_region(self):
        """Physical-pixel rect of the game viewport. Uses the aligned rect once align() has run."""
        if self.headless:  # no window: frames come from the page (screencast) at the fixed 720p viewport
            return (0, 0, 1280, 720)
        if self._region:
            return self._region
        L, T, R, B = find_window_region(self.title)
        if self.slot is not None:  # --app window: Chrome draws its title bar inside the client area; viewport at bottom
            iw, ih, dpr = self.page.evaluate("[innerWidth, innerHeight, devicePixelRatio]")
            vw, vh = int(round(iw * dpr)), int(round(ih * dpr))
            if vw * 9 > vh * 16:  # canvas is 16:9, centered horizontally if the viewport is wider
                cw = vh * 16 // 9
                x0 = L + (R - L - vw) // 2 + (vw - cw) // 2
                return (x0, B - vh, x0 + cw, B)
            ch = vw * 9 // 16
            x0 = L + (R - L - vw) // 2
            return (x0, B - vh + (vh - ch) // 2, x0 + vw, B - vh + (vh - ch) // 2 + ch)
        w = R - L
        vw = w - (w % 16)
        vh = vw * 9 // 16
        return (L + (w - vw) // 2, B - vh, L + (w - vw) // 2 + vw, B)

    def align(self):
        """Find the viewport inside the window by matching a page screenshot against a screen grab
        of the whole client area (browser chrome heights and DPI rounding vary per machine)."""
        import cv2
        import numpy as np
        from vision.capture import Capture
        self._region = None
        L, T, R, B = find_window_region(self.title)
        for _ in range(20):  # the game fades in from black; matching a flat frame is meaningless
            shot = cv2.imdecode(np.frombuffer(self.page.screenshot(), np.uint8), cv2.IMREAD_COLOR)  # 1280x720 CSS
            if float(shot[180:540, 320:960].std()) > 12:
                break
            time.sleep(0.5)
        cap = Capture((L, T, R, B), 30, backend="mss")
        full = cap.grab()
        cap.close()
        best = None
        for scale in np.arange(1.0, 2.01, 0.25):  # DPI scale candidates
            vw, vh = int(round(1280 * scale)), int(round(720 * scale))
            if vw > full.shape[1] or vh > full.shape[0]:
                break
            tpl = cv2.resize(shot, (vw, vh))[vh // 4: 3 * vh // 4, vw // 4: 3 * vw // 4]
            res = cv2.matchTemplate(full, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if best is None or score > best[0]:
                best = (score, scale, loc[0] - vw // 4, loc[1] - vh // 4, vw, vh)
        score, scale, x, y, vw, vh = best
        region = (L + x, T + y, L + x + vw, T + y + vh)
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        on_screen = region[0] >= 0 and region[1] >= 0 and region[2] <= sw and region[3] <= sh
        if not (0.8 < score <= 0.999 and on_screen):
            self.log(f"[game] alignment rejected (match {score:.2f}, {region}); using geometric estimate")
            return self.canvas_region()
        self._region = region
        self.log(f"[game] viewport aligned: {self._region} scale {scale} match {score:.2f}")
        return self._region
