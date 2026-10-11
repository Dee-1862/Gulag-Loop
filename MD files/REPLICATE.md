# Run the zombie bot on your PC

This bot plays Black Ops 1 Zombies (Kino der Toten) in the browser port at https://vel.gg/bo1z. It finds zombies
with a small vision model running on your own GPU and plays with an evolved strategy from `strategies.json`.

## You need
- Windows 10/11 and an NVIDIA GPU (tested on an RTX 3060 Laptop, 6 GB)
- Google Chrome
- Python 3.12 (python.org installer, with "py launcher" ticked)
- ~8 GB free disk (PyTorch CUDA is big), and internet for the first game load
- The laptop **plugged in**

## Setup (once, ~10 min)
```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
.\.venv\Scripts\python -m bot.worker calibrate
```
`calibrate` opens Chrome, loads Kino (~50 s) and turns the camera a few times to measure your mouse-to-screen
ratio. It writes `calibration.json`. **Don't touch the mouse while it runs.** Redo it if you change in-game
sensitivity or display scaling.

## Play
```powershell
.\.venv\Scripts\python -m bot.worker run --strategy strategies.json --name best --games 3 --results my_results.json
```
- The bot needs the game window **in front**. It uses your real mouse while a game runs, and if you click away it
  pulls the window back.
- **F8** ends the current game. **Esc** in game releases the mouse.
- Each game lasts up to 3 min, or until you die. Results (points, kills, survival, diagnostics) go to
  `my_results.json`, and a HUD video of each game goes to `runtime\episodes\`.
- Other styles in `strategies.json`: `baseline`, `aggressive`, `conservative`, `sniper`, `kiter`.

## What "best" is, honestly
`best` = the **Aggressive** profile, the top mean fitness (points + 5 x survival seconds) so far in the final
experiment: **2 games, 0 and 3 kills, mean fitness 481** (the baseline scored 445). The sample is small and
game-to-game variance is large, so expect anywhere from 0 to ~3 kills per round-1 game. The evolution run was
paused early. See `measured` in `strategies.json`.

## What the strategy numbers mean
| param | meaning |
|---|---|
| `det_conf` | how sure the detector must be that something is a zombie (lower = sees more, including false positives) |
| `aim_gain` | fraction of the aim error corrected per frame (scaled by your calibration) |
| `aim_max_step` | max mouse counts per frame |
| `aim_y_offset` | where on the body to aim, from the top of the box (0 = head) |
| `fire_radius_px` | fire when the aim point is this close to the crosshair (720p pixels), or when the crosshair is on the body |
| `burst_ms` | how long each trigger pull lasts |
| `retreat_box_h` | back up when the zombie's box is taller than this (closer = bigger) |
| `strafe_mode` / `strafe_period_s` | side-stepping while fighting |
| `reload_when_clear_at` | reload when no zombie is in view and the magazine is at or below this |
| `scan_turn_px` | how fast to sweep the view when nothing is visible (px/s) |
| `target_rule` | which zombie to pick: `nearest`, `largest` or `center` |

The controller also always does the following: it reacts to damage (red screen) with a fast turn toward the
unseen attacker, backs off when hit while fighting, holds one target at a time, and compensates for input latency.

## Optional
- **Watch the detector on a video instead of the game:** `.\.venv\Scripts\python -m vision.run --source some_clip.mp4 --record out.mp4`
- **Evolve your own strategies with LLM agents:** needs the `evolution.py` code from the team repo (branch `jeffrey`)
  and your **own** Luminal API key in that folder's `.env`. Then run `python -m bot.loop --evo-dir <that folder> --seed-profiles`.
- **Change where runtime files go:** set `CZB_HOME` (default: `.\runtime`).

## Troubleshooting
- *Chrome uses the wrong GPU:* the bot launches Chrome with `--force_high_performance_gpu`. The log line
  `gpu: ANGLE (NVIDIA ...)` should name your NVIDIA card.
- *Camera doesn't turn:* the game ignores mouse look for ~2 s at spawn (the "Round" banner). If it never turns, rerun
  `calibrate`.
- *Slow (<20 fps in the HUD):* plug in, close other GPU apps. If TensorRT failed in setup, the `.pt` fallback is ~3x slower.
