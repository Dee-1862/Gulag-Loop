# Zombies Agent: On-Device Vision

The bot's eyes. It captures the game window, finds zombies with a small YOLO model running on the laptop GPU (TensorRT), keeps an ID on each zombie from frame to frame, picks a target, and gives `controller.py` the aim error in pixels. It also draws a tactical HUD over the game for the demo video.

The plan for the whole project is in [`MD files/cod-zombies-agent-plan.md`](MD%20files/cod-zombies-agent-plan.md). This README covers the vision side (plan §5.2 and §5.4, Gate 2).

![HUD](docs/img/hud_acquiring.jpg)

---

## TL;DR

| | Result |
|---|---|
| Inference speed | **5.9 ms/frame** end to end with TensorRT FP16 on an RTX 3060 Laptop (~170 fps). PyTorch took 20–29 ms. |
| Capture | `dxcam` at 60 fps (its configured cap), `mss` fallback at 52 fps, for a 1280×720 region |
| Gate 2 with the stock COCO "person" model | **Fails.** It finds a zombie in only 21–37% of frames, misses obvious ones, and boxes the player's own hands. |
| Fix | Distillation: YOLOE-L ("zombie") auto-labels gameplay footage, then a yolo11n student trains on those labels. No hand labeling. |
| Student (`zombies_v1`) | Best epoch 39: P 0.62 / R 0.55 / mAP50 0.58 against the teacher's labels on a held-out clip |
| Held-out before/after | Locks onto something in 42% of frames vs 22% for COCO. This counts any lock, false positives included. Proper P/R for both models is still pending. |

**This is a first working version, not a finished detector.** See [Known gaps](#known-gaps--next-steps).

---

## Layout

```
config.yaml              capture window, model path, masks (calibrate per machine)
requirements.txt
vision/
  capture.py             dxcam/mss screen grab, DPI-aware window finder
  detector.py            YOLO wrapper (.pt or TensorRT .engine), masks, corpse filter, Box
  tracker.py             IoU tracker: stable IDs, velocity, sticky target lock
  hud.py                 tactical HUD renderer (~3 ms/frame)
  pipeline.py            VisionPipeline: the one-call interface for controller.py
  run.py                 live/recorded viewer, Gate 2 test, h264 recorder
  autolabel.py           teacher (YOLOE-L) auto-labels footage into a YOLO dataset
  train.py               trains the yolo11n student and exports TensorRT
  compare.py             before/after side-by-side reel for the video
docs/img/                screenshots used in this README
```

**Not in git:** model weights (`*.pt`, `*.engine`, `*.onnx`), datasets, footage and renders. They live on `D:\czb\` on Rishit's laptop (see [Large files](#large-files-on-d)).

---

## Setup (Windows + NVIDIA GPU)

Use a venv on a drive with space. Torch with CUDA is a ~2.8 GB wheel and needs about 7 GB installed.

```bash
python -m venv D:\czb\venv
# Git Bash sets TMPDIR, and Python reads it before TEMP. Point all three at D or pip unpacks onto C.
export TMPDIR='D:\czb\tmp' TMP='D:\czb\tmp' TEMP='D:\czb\tmp' PIP_CACHE_DIR='D:\czb\pipcache'
D:\czb\venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
D:\czb\venv\Scripts\python -m pip install -r requirements.txt
D:\czb\venv\Scripts\python -m pip install "tensorrt-cu12>=10.9,<11" onnx==1.19.1 onnxslim imageio-ffmpeg
D:\czb\venv\Scripts\yolo settings weights_dir='D:\czb\models' datasets_dir='D:\czb\datasets' runs_dir='D:\czb\runs'
```

**Plug the laptop in and use the Best performance power plan.** On battery, the same model took 22 ms per frame instead of 6.

---

## Usage

```bash
# Gate 2 on recorded footage, with HUD, saved to h264 mp4
python -m vision.run --source D:/czb/footage/kino_b.mp4 --record out.mp4

# Live on the game window (the title substring is set in config.yaml)
python -m vision.run
#   keys: q quit | s save frame | a auto-save 1/s (for labeling) | [ ] conf | m show masks | p pause

# Before/after reel for the video
python -m vision.compare --source D:/czb/footage/kino_solo_mid.mp4 \
    --a D:/czb/models/yolo11n.engine --b D:/czb/models/zombies_v1.engine --record before_after.mp4
```

### From `controller.py`

```python
from vision.pipeline import VisionPipeline
vp = VisionPipeline.from_config("config.yaml")             # opens a live capture of the game window
while running:
    obs = vp.step(conf=S.det_conf, rule=S.target_rule, aim_y_offset=S.aim_y_offset)
    if obs.target:
        dx = clamp(obs.err_x * S.aim_gain, S.aim_max_step)  # err_x/err_y = aim point minus crosshair, in px
        ...
    log(obs.telemetry)      # n_boxes, n_tracks, target_id, err_x/y, target_h_frac, nearest_h_frac, lock_frames, infer_ms
    frame = vp.hud_frame(info={"action": "ENGAGE", "generation": 3, "persona": "Bold", "points": 1240})
```

`target_rule` (`nearest` / `largest` / `center`) and `aim_y_offset` map straight onto the strategy genome in §5.1 of the plan. Once `config.yaml` points at the distilled model, set `classes: null`. The default `[0]` means COCO "person".

---

## What was done, and why

### 1. Environment
- The installed torch was a **CPU-only build**, so YOLO would have run at under 10 fps. I installed CUDA torch (cu128) instead.
- **The C: drive was full** (2.4 GB free), and the first install failed. Everything now lives on `D:\czb\`: venv, pip cache, temp, models, datasets, footage, renders.
- The **TensorRT 11** export needs NVIDIA's `modelopt` package for FP16. Ultralytics auto-installed it, which broke the `onnx` package. I downgraded to **TensorRT 10** (which still has the FP16 builder flag), removed `modelopt` and pinned `onnx==1.19.1`.

### 2. Speed
| Config | ms/frame |
|---|---|
| PyTorch FP16, on battery | 26–29 |
| PyTorch FP16, plugged in | 13–20 |
| **TensorRT FP16 engine** | **5.9** |

On the plugged-in PyTorch run, the GPU sat at P5 / 645 MHz: launching operations from Python was the bottleneck, not the GPU's math. TensorRT removes that.

### 3. Gate 2 with pretrained COCO "person": fails
I tested on ~22 min of real Kino der Toten footage (5 YouTube clips, early and mid rounds), with HUD and gun masks applied:

| conf | frames with a detection (early rounds) | (mid rounds) |
|---|---|---|
| 0.15 | 51% | 36% |
| 0.25 | 37% | 21% |
| 0.40 | 32% | 9% |

It misses clearly visible zombies and fires on scenery:

![pretrained](docs/img/pretrained_coco_sheet.jpg)

### 4. Teacher to student distillation (no hand labeling)
1. **Teacher:** YOLOE-11L, an open-vocabulary model prompted with `["zombie", "person"]`. It finds something in 92% of frames, at ~200 ms/frame. On raw frames it also boxes the **player's own hands** (conf ~0.8), so frames get the same masks as at runtime before labeling.
2. **Filters:** drop boxes wider than 1.3× their height (corpses on the floor) and tiny boxes. Merge duplicate boxes (one object labeled both "zombie" and "person", or a head box inside a body box): 2,362 → 2,239 boxes.
3. **Dataset:** 2,469 frames sampled every 0.25 s, 1,512 of them with zombies. A whole clip (`kino_solo_mid`) is held out as validation, so validation frames aren't near-copies of training frames.
4. **Student:** yolo11n from COCO weights, 60 epochs at 640 px, ~16 min on the 3060. Best epoch 39: **P 0.62, R 0.55, mAP50 0.58, mAP50-95 0.37**. Exported to TensorRT FP16.

Teacher labels (spot check, mostly real zombies plus some noise like a wall portrait):

![labels](docs/img/teacher_labels_sheet.jpg)

Training curves:

![curves](docs/img/student_training_curves.png)

Before/after on the held-out clip (left: COCO, right: student). The student never locked onto Pack-a-Punch, lightning or the damage screen, but both models still miss zombies in heavy fog:

![before after](docs/img/before_after_sheet.jpg)

### 5. Tracker
`tracker.py` matches boxes across frames by overlap, with a center-distance fallback for fast-moving boxes. It coasts a lost zombie for up to 6 frames and only switches targets when another zombie scores 1.3× better. Without it, the target flips between zombies, the aim error changes sign, and that gets counted as **overshoot** (plan §5.4). The evolution agents would then tune against a problem that isn't real.

### 6. HUD (for the video)
- **Each zombie:** corner brackets colored by threat (cyan, then amber, then red as it gets closer), plus ID, confidence and distance (`Z-07  87%  4.2m`, estimated from box height), a motion trail and a velocity arrow.
- **Target lock:** rotating reticle that goes ACQUIRING → LOCKED → FIRE, a dashed line from the crosshair, and the error in pixels.
- **Screen panels:** telemetry (fps sparkline, latency, GPU), a 16-segment THREAT meter with a flashing DANGER CLOSE, a radar cone with a sweep, an action banner (ENGAGE / RETREAT / SCAN), and an optional agent strip (generation, persona, points).
- **Look:** vignette, scanlines and a cool tint. Text uses cached Bahnschrift/Consolas renders, about 3 ms per frame.

![danger](docs/img/hud_danger_close.jpg)

---

## Known gaps / next steps
1. **The footage is the original game, not the browser port.** If `vel.gg/bo1z` looks different, the student may not carry over. The pipeline reruns in ~30 min: press `a` in `vision.run` to save port frames, then run `vision.autolabel --videos <recordings>` and `vision.train`.
2. **Fog and darkness:** both models miss zombies in mid-round fog. Options: more data, more mid-round clips, or `imgsz 960` (TensorRT should still be under 10 ms).
3. **The gun mask hides point-blank zombies.** It covers the bottom-right ~45% of the screen, which shrinks the box height of close zombies, so `retreat_box_h` and `close_calls` under-trigger. Calibrate the masks on the port (`m` shows them).
4. **Proper before/after precision/recall** for COCO vs the student on the held-out set is still running and isn't in this README.
5. **HUD distance and radar** assume an 1.8 m zombie and a ~59° vertical / 90° horizontal FOV. They're for looks, not measurements.
6. **The student copies the teacher's mistakes.** Its labels come from YOLOE, so mAP measures agreement with YOLOE, not with ground truth.
7. Not done yet: storing the weights in MongoDB GridFS (plan §2). Upload `zombies_v1.pt` there when the Mongo layer is ready.

---

## Large files on D
```
D:\czb\venv\                       Python env (torch cu128, ultralytics 8.4.175, TensorRT 10.16)
D:\czb\models\yolo11n.{pt,engine}  COCO baseline
D:\czb\models\zombies_v1.{pt,engine,onnx}   distilled student  <- point config.yaml here
D:\czb\models\yoloe-11l-seg.pt, mobileclip_blt.ts   teacher
D:\czb\datasets\zombies\           auto-labeled dataset (data.yaml)
D:\czb\footage\                    Kino clips used for labeling
D:\czb\runs\zombies_v1\            training run, curves, confusion matrix
D:\czb\renders\                    HUD test + before_after.mp4
```
TensorRT engines are tied to one GPU and TensorRT version. Rebuild them on each game laptop from the `.pt` with `YOLO("zombies_v1.pt").export(format="engine", half=True)`.
