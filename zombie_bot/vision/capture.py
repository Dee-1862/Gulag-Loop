"""Screen capture for the game window. dxcam (Desktop Duplication) with an mss fallback."""
import ctypes
import ctypes.wintypes as wt

import numpy as np

# Physical pixels everywhere. Without this, laptops at 125/150% scaling report
# window rects that don't match what dxcam captures.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

user32 = ctypes.windll.user32


def find_window_region(title_substr):
    """Return (left, top, right, bottom) of the client area of the first visible
    window whose title contains title_substr (case-insensitive), or None."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_substr.lower() in buf.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        return None
    hwnd = found[0]
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return (pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom)


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


class Capture:
    """Grab BGR frames of a screen region. region=(left, top, right, bottom) or None for the full primary monitor."""

    def __init__(self, region=None, target_fps=60, backend="dxcam"):
        self.region = region
        self.backend = backend
        if backend == "dxcam":
            try:
                import dxcam
                self.cam = dxcam.create(output_color="BGR")
                self.cam.start(region=region, target_fps=target_fps, video_mode=True)
                return
            except Exception as e:
                print(f"[capture] dxcam failed ({e}); falling back to mss")
                self.backend = "mss"
        import mss
        self.sct = mss.mss()
        if region is None:
            m = self.sct.monitors[1]
            self.mon = {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
        else:
            l, t, r, b = region
            self.mon = {"left": l, "top": t, "width": r - l, "height": b - t}

    def grab(self):
        if self.backend == "dxcam":
            return self.cam.get_latest_frame()  # blocks until a new frame
        return np.ascontiguousarray(np.array(self.sct.grab(self.mon))[:, :, :3])

    def close(self):
        if self.backend == "dxcam":
            self.cam.stop()
