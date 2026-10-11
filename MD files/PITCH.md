# Pitch

## One line
**On-device AI plays a real game in real time; a team of Luminal agents rewrites its strategy from the evidence; MongoDB keeps every decision, run and replay.** Automated playtesting today, any real-time controller tomorrow.

## 30-second version
Game studios pay people to play builds over and over. We built an agent that does it: a 2.6 M-parameter vision model on the laptop GPU sees zombies at ~30 decisions per second, a controller plays the browser port of Black Ops Zombies, and between games four Luminal agents (Bold, Tuner, Defender, Explorer) read the run diagnostics, explain what went wrong and propose new strategies. Each proposal is played for real, scored, and kept only if it wins. Every step is stored in MongoDB and shown live on our dashboard with the agents' reasoning and the game replays.

## Measured results (exp-kino-2, real games, from MongoDB)
| | Generation 0 (baseline) | Generation 2 (evolved) |
| --- | --- | --- |
| Average survival | 49.0 s | **61.8 s** |
| First zombie seen | 19.2 s | **9.1 s** (2x faster) |
| Detections per frame | 0.06 | **0.41** (~7x) |
| Best fitness (points + 5 x survival) | 276 | **372** (gen 1 champion) |

Luminal: 9 agent calls, 8 succeeded first time, 0 fallbacks. The agents found the bottleneck themselves: *"The worst diagnostic is the 89.8% no-target time ... so I sharply lower det_conf to 0.18 and raise scan_turn_px to 240."* Two agents independently lowered the detection threshold; perception improved in the next generation.

## Luminal: why it matters here
- **The track asks for agents that propose, test and learn with measurable improvement.** Ours propose in parallel, every proposal is tested in the real game (not a simulator), and the learning is visible: reasoning tied to measured diagnostics, scores per generation.
- **The loop is Luminal's own idea applied to behaviour:** Luminal's compiler searches over candidate kernels, measures them, keeps the fastest. We search over strategies, measure them in the game, keep the best.
- **Speed and cost make it practical:** four agents per generation, retries and validation on every call, every call logged (model, latency, tokens) for the audit trail.

## AGI, Inc.: why it matters here
- **A real-time on-device computer-use agent.** It perceives the screen and acts on the app by itself at ~30 decisions/s; a typical cloud computer-use agent takes seconds per action.
- **Fully on device during play:** detector 2.6 M params (5.5 MB), TensorRT FP16 ~4-8 ms/frame on an RTX 4060 laptop GPU, zero cloud calls while playing; the cloud is only used between games to rethink strategy.
- **Distilled to be small:** a large open-vocabulary teacher (YOLOE-11L) labeled gameplay; our tiny student learned from it (knowledge distillation, Hinton et al. 2015). Exported to ONNX, the same model can target CPUs, NPUs (AMD Ryzen AI, Qualcomm Snapdragon) and browsers (ONNX Runtime Web). INT8 post-training quantization (Jacob et al. 2018) is scripted next.
- **Runs headless:** no game window; you watch it live in the dashboard.

## MongoDB
One shared memory for three laptops: runs, jobs (atomic claims), strategies with lineage, every agent call, team calibration, and every episode video (GridFS, tagged by generation) — the evidence behind each number above. Each generation is saved in one transaction.

## Honest limits (say them before judges ask)
- The bot sees and tracks zombies but **hasn't landed kills yet** (aiming fix in progress), so the gains are in perception and survival, not points.
- Small sample: 12 games over 2 generations on one map. A held-out-map test is built (`scripts/heldout_eval.py`) but not run to completion.
- Unofficial fan port of the game; we link to it and ship none of its files.
