"""In-page input for the browser port: no OS mouse, no foreground window, so several games can run at once.

The port's input.js reads `document.pointerLockElement === canvas` for "locked", `movementX/Y` from any
mousemove, keys from keydown `event.code`, and buttons from mousedown/mouseup. It never checks isTrusted.
INIT_SCRIPT (added before page scripts) fakes pointer lock and exposes window.__bot to send synthetic events.
"""

INIT_SCRIPT = r"""
(() => {
  // background tiles: the page must believe it is focused and visible, or the game ignores input
  Document.prototype.hasFocus = () => true;
  Object.defineProperty(Document.prototype, 'visibilityState', { configurable: true, get: () => 'visible' });
  Object.defineProperty(Document.prototype, 'hidden', { configurable: true, get: () => false });
  const addEL = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, ...rest) {
    if (type === 'blur' || type === 'visibilitychange' || type === 'focusout') return;
    return addEL.call(this, type, ...rest);
  };
  let lockEl = null;
  const fire = () => document.dispatchEvent(new Event('pointerlockchange'));
  Object.defineProperty(Document.prototype, 'pointerLockElement', { configurable: true, get() { return lockEl; } });
  Element.prototype.requestPointerLock = function () { lockEl = this; setTimeout(fire, 0); return Promise.resolve(); };
  Document.prototype.exitPointerLock = function () { if (lockEl) { lockEl = null; setTimeout(fire, 0); } };
  const codeOf = k => k.length === 1 ? (/[a-z]/i.test(k) ? 'Key' + k.toUpperCase() : (/[0-9]/.test(k) ? 'Digit' + k : k)) : k;
  window.__bot = {
    locked: () => !!lockEl,
    lock() { const c = document.getElementById('game'); if (c) c.requestPointerLock(); return !!c; },
    move(dx, dy) { document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, movementX: dx, movementY: dy })); },
    key(k, down) {
      const code = codeOf(k);
      document.dispatchEvent(new KeyboardEvent(down ? 'keydown' : 'keyup', { bubbles: true, cancelable: true, code, key: k }));
    },
    button(b, down) {
      const c = document.getElementById('game') || document;
      c.dispatchEvent(new MouseEvent(down ? 'mousedown' : 'mouseup', { bubbles: true, cancelable: true, button: b }));
    },
    // one call per frame: [[op, ...args], ...]
    batch(ops) { for (const [op, a, b] of ops) this[op](a, b); },
  };
})();
"""


class JSKeys:
    """Same interface as controller.Keys, but events go into one page (batched once per frame)."""

    def __init__(self, page):
        self.page = page
        self.held = set()
        self.mouse_down_until = 0.0
        self.rx = self.ry = 0.0
        self.sent_x = self.sent_y = 0
        self.ops = []

    def hold(self, key, on):
        if on and key not in self.held:
            self.ops.append(["key", key, True])
            self.held.add(key)
        elif not on and key in self.held:
            self.ops.append(["key", key, False])
            self.held.discard(key)

    def move(self, dx, dy):
        self.rx += dx
        self.ry += dy
        ix, iy = int(self.rx), int(self.ry)
        # input.js drops single deltas > 300 as spurious; split big moves
        ix = max(-280, min(280, ix))
        iy = max(-280, min(280, iy))
        if ix or iy:
            self.ops.append(["move", ix, iy])
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
            self.ops.append(["button", 0, True])
            self.mouse_down_until = now + ms / 1000.0
            return True
        return False

    def tick(self, now):
        if self.mouse_down_until and now >= self.mouse_down_until:
            self.ops.append(["button", 0, False])
            self.mouse_down_until = 0.0

    def press(self, key):
        self.ops.append(["key", key, True])
        self.ops.append(["key", key, False])

    def flush(self):
        if self.ops:
            ops, self.ops = self.ops, []
            self.page.evaluate("ops => window.__bot.batch(ops)", ops)

    def release_all(self):
        for k in list(self.held):
            self.ops.append(["key", k, False])
        self.held.clear()
        self.ops.append(["button", 0, False])
        try:
            self.flush()
        except Exception:
            pass
