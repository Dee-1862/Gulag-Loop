# Self-Improving Zombies Agents — Build Plan

**Event:** c0mpiled-15 @ EDI, Sun Oct 11 · build until 8:00 PM, judging 8:00
**Target:** Track 1A (Luminal self-improving agent team) + MongoDB prize + AGI on-device prize
**Game:** Black Ops 1 Zombies browser port (`vel.gg/bo1z`), map Kino der Toten
**Deliverable:** a ~90s video (no live demo)

---

## 0. One-paragraph pitch

A team of 4 LLM agents (running on Luminal Cloud) evolves a bot that plays Call of Duty Zombies. The bot's eyes and reflexes run fully on-device: a local YOLO model spots zombies and a controller aims and fires at 30 fps. After every 3-minute run, the agents read the bot's telemetry ("overshot 60% of flicks", "got hit with a zombie at point-blank 9 times"), propose new strategies in parallel, and the best ones survive. Every strategy, score and lineage lives in MongoDB. We show round-1 bots dying instantly, then evolved bots surviving, and prove it on a map the agents never trained on.

---

## 1. What we verified (and what we didn't)

| Claim | Status | Notes |
|---|---|---|
| BO1 Zombies runs in a browser, all maps incl. DLC | ✅ Verified | Needs browser hardware acceleration on to be playable. Fan port, unofficial. |
| `pydirectinput` sends input games accept | ✅ Verified | Uses DirectInput scan codes + `SendInput()`, built for games where PyAutoGUI fails. |
| Accurate relative mouse moves | ✅ Verified | Use the `pydirectinput-rgx` fork: `moveRel(..., relative=True, disable_mouse_acceleration=True)`. |
| Fast screen capture on Windows | ✅ Verified | `dxcam` (Desktop Duplication API), >240 Hz, built for FPS-game deep learning pipelines. Supports region capture. |
| Chrome FPS mouse-look reads raw input | ✅ Verified | Chrome pointer lock with `unadjustedMovement: true` reads raw mouse input. |
| SendInput moves turn the camera in *this* game | ⚠️ **Must test (Gate 1)** | Strong expectation it works (SendInput produces real OS input), but this is the single biggest risk. Test it first. |
| Luminal Cloud has an LLM API with DeepSeek / GLM Flash | ⚠️ **Ask organizers** | Luminal's public site describes serverless inference endpoints but I couldn't find public API docs. Get the base URL, key, and exact model names from the Luminal table at the event. Write the client so these are env vars. |
| YOLO pretrained "person" class detects BO1 zombies | ⚠️ **Must test (Gate 2)** | Zombies are humanoid, so likely yes at lower confidence. Fine-tune fallback below. |
| AGI Inc has no public model | ✅ (told by AGI team) | Any on-device vision model counts. Ours = YOLO11n on the laptop GPU. |

---

## 2. Correction on "MongoDB stores weights"

We aren't training neural-net weights in the loop. What evolves is each bot's **strategy**: ~10 numeric params + a few rule choices (aim speed, fire threshold, retreat distance, reload timing...). In MongoDB, a strategy document *is* the bot's "weights." 

To make the line literally true too: if we fine-tune YOLO (fallback in §8), store the `.pt` file in **MongoDB GridFS**. Then Mongo holds both the vision weights and every evolved strategy.

---

## 3. Stress test: will the evolution loop actually improve?

I simulated the loop offline (toy fitness landscape over 5 strategy params, 3-minute-run scores with heavy random noise, 4 agents per generation, 400 trials per setting).

**Result 1: blind mutation fails.** If agents only see the score and tweak params randomly, 8 generations gives basically no improvement (median gain ≈ 0–90 points, and 17–37% of trials ended *worse* than baseline). More runs barely helps when noise is high.

| Setting (noise ±300 pts/run) | Median gain | P(clearly better) | P(worse than baseline) |
|---|---|---|---|
| Blind mutation, 8 gens | ~6 | 12% | 30% |
| Blind + champion re-eval, 12 gens | ~41 | 29% | 19% |
| **Log-guided agents, 6 gens** | **~1800** | **100%** | **0%** |

**Result 2: guided agents work, even with weak hints.** When the agents get diagnostics that point the right way only 60% of the time, every trial improved clearly within 6 generations, even at ±500 noise. (The toy model is optimistic, so treat the exact numbers loosely. The direction is the finding.)

**What this means for the build — non-negotiable:**
1. **Telemetry is the product.** The controller must log diagnostics the LLM can reason about (§5.4). Score alone is useless.
2. **Re-run the current champion every generation** and average, so one lucky run can't stay on top.
3. **Keep 2 parents** (top-2) so one bad generation can't wipe progress.
4. **Personas = different step sizes** (bold vs. careful), so the team explores and refines at the same time.

---

## 4. Architecture

```
┌──────────────────── GAME LAPTOP (×2–3, Windows + GPU) ─────────────────────┐
│ Chrome (HW accel ON, 1280×720 window) ── vel.gg/bo1z ── Kino der Toten     │
│        ▲ mouse/keys (pydirectinput-rgx)          │ frames (dxcam, region)  │
│        │                                         ▼                          │
│   controller.py  ◄── strategy.json         YOLO11n on GPU (on-device)       │
│   (30 fps loop: detect → aim → fire → move)                                 │
│        │ telemetry.jsonl + run summary + OCR score                          │
└────────┼────────────────────────────────────────────────────────────────────┘
         ▼
   worker.py  ── pulls a queued strategy from Mongo, runs 1 episode, writes results
         ▲
         │                    MongoDB Atlas (strategies, runs, llm_calls, GridFS)
         ▼
┌──────────────────── ORCHESTRATOR LAPTOP (no game) ─────────────────────────┐
│ orchestrator.py: each generation                                            │
│   1. read top-2 strategies + their telemetry summaries                      │
│   2. 4 persona agents call Luminal (DeepSeek / GLM Flash) IN PARALLEL       │
│   3. validate JSON → clamp → enqueue 4 children + champion re-eval          │
│   4. wait for workers → rank → next generation                              │
│ dashboard.py: score-per-generation chart + lineage tree (from Mongo)        │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Why this split:** LLMs are seconds-slow, aiming needs milliseconds. LLMs never touch the controls; they only write strategy JSON between runs.

---

## 5. Module specs (hand these to your coding AI)

### 5.1 `strategy.json` schema (the evolvable genome)

All values clamped to bounds before use. LLM output that fails validation → retry once → else fallback to a small random mutation of the parent.

| Param | Type | Bounds | Meaning |
|---|---|---|---|
| `det_conf` | float | 0.15–0.70 | YOLO confidence threshold |
| `aim_gain` | float | 0.1–2.0 | mouse px per px of target error |
| `aim_max_step` | int | 5–200 | max mouse move per frame (anti-overshoot) |
| `aim_y_offset` | float | 0.0–0.5 | aim point from top of box (0 = head) |
| `fire_radius_px` | int | 5–80 | fire when target center within this of crosshair |
| `burst_ms` | int | 30–400 | hold-fire duration per shot |
| `retreat_box_h` | int | 50–500 | backpedal when nearest zombie box taller than this (= close) |
| `strafe_mode` | enum | `none`,`left`,`right`,`alternate` | movement while engaging |
| `strafe_period_s` | float | 0.5–5.0 | alternate strafe period |
| `reload_when_clear_at` | int | 0–8 | reload when no targets and mag below this |
| `scan_turn_px` | int | 0–300 | turn this much per second when no zombie visible |
| `target_rule` | enum | `nearest`,`largest`,`center` | which zombie to pick |

Fields stored alongside: `_id`, `parent_ids`, `generation`, `persona`, `model`, `rationale` (LLM's 1–2 sentence reason — great for the video).

### 5.2 `controller.py` (fast loop, ~30 fps)

```
setup:
  camera = dxcam.create(); camera.start(region=GAME_REGION, target_fps=60)
  model = YOLO("yolo11n.pt").to("cuda")   # or fine-tuned zombies.pt
  register global hotkey F8 = ABORT (release all keys, stop loop)   # mouse is hijacked — must have this
loop until 180 s or death detected or F8:
  frame = camera.get_latest_frame()
  boxes = model(frame, classes=[0], conf=S.det_conf, verbose=False)
  target = pick(boxes, S.target_rule)
  if target:
      ex = target.cx - CX ; ey = (target.y1 + S.aim_y_offset*target.h) - CY
      dx = clamp(ex*S.aim_gain, ±S.aim_max_step); dy = same
      pydirectinput.moveRel(dx, dy, relative=True, disable_mouse_acceleration=True)
      if hypot(ex,ey) < S.fire_radius_px: fire(S.burst_ms)
      if target.h > S.retreat_box_h: hold 's' else release 's'
      apply strafe_mode
  else:
      release 's'; turn by S.scan_turn_px * dt
      if mag_low and S.reload_when_clear_at: press 'r'
  log telemetry
every 2 s: OCR the points HUD crop → points
end: write run summary
```

Notes:
- Fire with `mouseDown` / `mouseUp` around `burst_ms`, not `click()`.
- Run YOLO at imgsz 640 on a downscaled frame; check loop fps in logs (target ≥ 20).
- Never let the loop run without the F8 kill switch. Also leave `pydirectinput.FAILSAFE = True` (slam mouse to a corner to stop).

### 5.3 Scoring

- **Primary score:** points gained in 180 s (OCR the points counter; `easyocr` or `pytesseract` on a fixed crop, digits whitelist, take the max of the last 3 readings to smooth OCR misses).
- **Secondary:** survival seconds (if downed before 180 s).
- **Fitness = points + 5 × survival_seconds.** Store all raw parts.
- Calibrate the HUD crop once on the game laptop; save coordinates in `config.yaml`.

### 5.4 Telemetry → diagnostics (the most important module)

Per run, compute and store these. They become the agents' prompt.

| Diagnostic | How to compute |
|---|---|
| `overshoot_rate` | fraction of aim frames where sign(ex) flips next frame |
| `avg_frames_to_lock` | frames from target acquired → within fire radius |
| `shots_without_target` | fire events with no box in frame |
| `hits_proxy` | points increase events (each OCR delta ≈ hits) |
| `close_calls` | frames with nearest `box.h > 0.6 × screen_h` |
| `retreat_frames` / `close_calls` | is retreat actually triggering |
| `time_no_target_pct` | % frames with zero detections (scan rule weak or detector blind) |
| `det_count_avg` | avg boxes per frame (detector sanity) |
| `stuck_events` | frame-diff below threshold for >1 s while moving |
| `death_time_s`, `last_5s_summary` | what was happening right before going down |

Save 1 annotated keyframe per 10 s to disk (boxes drawn) for the video.

### 5.5 `orchestrator.py` (slow loop)

```
gen 0: enqueue BASELINE strategy (hand-written, deliberately mediocre) ×2 runs
for gen in 1..N:
  parents = top-2 by mean fitness (champion re-run every gen, averaged)
  in parallel, for persona in [Bold, Tuner, Defender, Explorer]:
      prompt = system(persona) + parent strategies + their diagnostics + score history
      resp = luminal.chat(model=MODEL, json mode, temperature by persona)
      child = validate_and_clamp(resp) or mutate(parent)
      log llm call to Mongo (model, latency, tokens, prompt hash)
  enqueue 4 children + 1 champion re-eval
  wait for workers; rank; update dashboard
```

**Personas (different step sizes = explore + exploit):**
- **Bold** — make 2–4 big changes to fix the worst diagnostic.
- **Tuner** — change 1–2 params by ≤15%.
- **Defender** — focus only on survival params (retreat, strafe, reload).
- **Explorer** — try a different `target_rule` / `strafe_mode` combo.

**Prompt skeleton:**
```
You tune a bot that plays CoD Zombies. You output ONLY JSON matching this schema: {...}.
Parent strategy: {...}   Fitness: 640 (mean of 2 runs)
Diagnostics: overshoot_rate 0.58, avg_frames_to_lock 14, close_calls 41, retreat triggered 3/41,
             time_no_target 37%, death at 112s while 2 zombies at point-blank, reloading.
History: gen1 640 → gen2 655 → gen3 650
Persona: Defender — change only survival-related params.
Return {"strategy": {...}, "rationale": "<1-2 sentences>"}.
```

### 5.6 `luminal_client.py`

- Assume OpenAI-compatible chat completions; configure `LUMINAL_BASE_URL`, `LUMINAL_API_KEY`, `LUMINAL_MODEL` via env. **Confirm all three with the Luminal team before writing anything else here.**
- Timeout 30 s, 2 retries with backoff, parse JSON robustly (strip code fences).
- If JSON mode isn't supported, instruct in the prompt and parse.
- Log every call to `llm_calls` (judges may ask for proof the required models were used).

### 5.7 MongoDB (Atlas free tier)

| Collection | Key fields |
|---|---|
| `strategies` | `_id, generation, persona, model, parent_ids[], params{}, rationale, status (queued/running/done), created_at` |
| `runs` | `_id, strategy_id, worker, map, fitness, points, survival_s, diagnostics{}, keyframes[], started_at` |
| `llm_calls` | `_id, generation, persona, model, latency_ms, tokens, ok, created_at` |
| `fs.files / fs.chunks` (GridFS) | YOLO weights if fine-tuned |

Workers claim jobs atomically: `find_one_and_update({status:"queued"}, {$set:{status:"running", worker:ID}})`.

### 5.8 `dashboard.py`

Single page (Streamlit is fastest): best and mean fitness per generation (line chart), lineage tree of the champion (graphviz from `parent_ids`), and the champion's `rationale` text per generation. This is screen-recorded for the video.

---

## 6. Red team / blue team

| # | Red team (what breaks) | Severity | Blue team (fix / fallback) |
|---|---|---|---|
| 1 | SendInput mouse moves don't turn the camera | Fatal | **Gate 1.** Try `pydirectinput-rgx` relative moves → raw win32 `SendInput` with `MOUSEEVENTF_MOVE` → toggle game's raw-input/sensitivity settings → try Edge. If none work by the gate: pivot (§8). |
| 2 | Game window loses focus → inputs go elsewhere | High | Click into the canvas at run start; check foreground window title each frame and pause if it changes. |
| 3 | YOLO misses zombies (dark, stylized) | High | **Gate 2.** Lower `det_conf` to 0.2. Fallback: grab ~150 frames, label in Roboflow (20 min), fine-tune YOLO11n ~10 min on the Zephyrus GPU. Store weights in GridFS. |
| 4 | YOLO detects the player's own gun/hands | Med | Mask the bottom-center region of the frame before detection. |
| 5 | Overshoot / oscillating aim from lag | Med | `aim_max_step` clamp + gain are evolvable params. `overshoot_rate` telemetry lets agents fix it. |
| 6 | Loop too slow (YOLO + capture + game on one GPU) | Med | 1280×720 window, imgsz 640 or 480, half precision, close everything else. Log fps; need ≥ 20. |
| 7 | Score noise drowns real progress | High | Champion re-eval every gen, top-2 parents, guided agents (§3). Show the **best-so-far** curve plus held-out comparison, which is robust. |
| 8 | LLM returns invalid / out-of-range JSON | Low | Pydantic validation + clamp + 1 retry + mutation fallback. Never crash the loop. |
| 9 | Luminal API unknown format / slow / rate-limited | High | Confirm at the table first. Agents run in parallel; generation waits on game runs anyway (minutes), so LLM latency doesn't matter. Cache last good response per persona. |
| 10 | OCR misreads the points | Med | Digit whitelist, fixed crop, max of last 3 reads, reject jumps > 2000 in 2 s. |
| 11 | Death / game-over not detected | Med | Template-match the game-over screen; fallback: points frozen 20 s + no detections → end run. Teammate can press F9 = "mark dead". |
| 12 | Map restart is manual | Low | Fine. Teammate restarts between runs (~30 s). Video won't show it. Automate later only if time. |
| 13 | The fan site gets taken down mid-day | High | **Record baseline + raw gameplay now.** Save some evolved-run footage as you go. Check if the page caches assets for offline replay. |
| 14 | Mouse hijacked → can't use the laptop | Low | Dedicated game laptops; F8 kill switch; orchestrator on another machine. |
| 15 | Bot gets stuck on walls / furniture | Med | Stay in Kino spawn room; `stuck_events` telemetry; scan rule turns away from walls. |
| 16 | Not enough generations | Med | Math: ~4.5 min per run incl. reset → ~13 runs/hr/laptop. 3 laptops ≈ 40 runs/hr ≈ **8 generations/hr** (5 runs/gen). Two hours of evolution = ~16 gens. Plenty. |
| 17 | Judges doubt the improvement is real | High | Held-out map never used in training; baseline vs. champion, 3 runs each, report mean. That's exactly the rubric. |
| 18 | Different laptops = different performance | Med | Same browser, resolution, sensitivity, YOLO weights on all. Compare fitness only within the same worker, or normalize per worker by re-running the baseline once on each. |

**What works in our favor:**
- Fast/slow loop split is the correct design and easy to explain.
- Early rounds = few, slow zombies = the zone where a simple bot can clearly improve.
- Rubric rewards problem difficulty; "LLMs teaching a bot to play CoD" is hard and obvious on video.
- Lineage tree + agent rationales make the "team of agents" story visible.
- One build covers 3 prizes.

---

## 7. Timeline (from 1:30 PM)

| Time | You (fast loop) | Teammate 2 (orchestrator) | Teammate 3 (scoring + video) |
|---|---|---|---|
| 1:30–2:15 | **Gate 1:** camera turns 90° via `moveRel`. Then F8 kill switch. | Luminal: get base URL/key/model names, make 1 call. Mongo Atlas up. | **Record baseline gameplay now.** Find HUD crop; OCR points working. |
| **2:15** | **GATE 1** — camera moves? No → pivot (§8). | | |
| 2:15–3:15 | YOLO boxes drawn live on gameplay (Gate 2). Aim + fire loop. | Strategy schema + validation; orchestrator with mocked runs. | Death detection; run summary writer. |
| **3:15** | **GATE 2** — bot kills a round-1 zombie? No → stationary turret bot (fire + turn only). | | |
| 3:15–4:15 | Telemetry diagnostics (§5.4); worker.py claims jobs from Mongo. | Personas + prompts; parallel calls; llm_calls logging. | Dashboard: fitness chart + lineage tree. |
| 4:15–4:30 | **End-to-end test:** 1 generation fully automated. Fix whatever breaks. | | |
| 4:30–6:30 | Evolution runs on all game laptops. Babysit restarts. | Watch prompts/rationales; fix bad agent behavior. | Screen-record dashboard + best runs as they happen. |
| 6:30–7:00 | Held-out map: baseline ×3 vs champion ×3. | Export final lineage + numbers. | Capture held-out side-by-side. |
| 7:00–7:45 | Video edit + submission. | Write submission text. | Edit. |

---

## 8. Pivot plan (if Gate 1 fails)

Same orchestrator, Mongo, personas and dashboard. Swap only the environment:
- **ViZDoom** (Python Doom, built for AI research): `pip install vizdoom`, game state + score from the API, no mouse hacking, runs headless and in parallel. Agents evolve the same kind of strategy JSON. Video is still a shooter.

---

## 9. Video script (~90 s)

| Sec | Shot |
|---|---|
| 0–8 | Hook: baseline bot spins and dies in round 1. Text: "Can a team of AI agents teach itself to play Call of Duty?" |
| 8–25 | Architecture diagram: on-device YOLO (AGI prize) · Luminal agents (DeepSeek/GLM Flash) · MongoDB (strategies + weights + lineage). |
| 25–50 | Timelapse of generations: fitness chart climbing; overlay agent rationales ("Overshooting 58% of flicks — cut aim gain, add step clamp"). |
| 50–62 | Lineage tree zooming into the champion. |
| 62–80 | Held-out map: baseline vs champion side by side. |
| 80–90 | Final numbers: "+X% points, Y× survival on an unseen map, Z generations, 4 agents." |

Tools: OBS for recording (capture YOLO-box overlay window too), any editor.

---

## 10. Pre-flight checklist

- [ ] Chrome hardware acceleration ON, same version on all game laptops
- [ ] Game window fixed at 1280×720, same in-game sensitivity everywhere
- [ ] Windows mouse acceleration off ("Enhance pointer precision")
- [ ] `pip install pydirectinput-rgx dxcam ultralytics easyocr pymongo pydantic openai keyboard opencv-python`
- [ ] F8 kill switch tested before first unattended run
- [ ] Baseline footage recorded
- [ ] Luminal base URL, key, model names confirmed
- [ ] Every LLM call logged with model name

---

## Sources

- Browser port: https://allthings.how/?p=244970
- pydirectinput (SendInput, scan codes): https://github.com/learncodebygaming/pydirectinput
- pydirectinput-rgx (relative moves, no accel): https://github.com/reggx/pydirectinput_rgx
- dxcam: https://github.com/ra1nty/DXcam
- Chrome pointer lock raw input: https://web.dev/articles/disable-mouse-acceleration
- Luminal: https://www.luminal.com
- Event page: https://luma.com/compiled-jjvw
