# Demo: 90-second video

Judges watch this without us there and may have the sound off. **Every shot needs on-screen captions,** and the result goes first.

## The story in one sentence

A coach on your laptop calls out what to buy while you play Zombies. Between games, a Luminal agent team evolves a better playbook. MongoDB remembers only the lessons that measurably helped.

## Shot list

| Time | On screen | Voice / caption | What's really happening | Proves |
| --- | --- | --- | --- | --- |
| 0:00–0:08 | Game full screen, dashboard on the right; a callout plays: *"2,600 points, buy Jugg now"* | "An AI coach that teaches itself, and forgets what doesn't work." | Perception read the HUD; the coach applied the best evolved playbook to the current state | It's real and live |
| 0:08–0:18 | Architecture diagram (laptop / MongoDB / Luminal) | "Play runs on the laptop. Learning runs in the cloud. MongoDB is the memory between them." | — | Each sponsor's role, at a glance |
| 0:18–0:33 | Close-up on the HUD with detected boxes; dashboard panel shows ms per frame and *cloud calls: 0*; **Wi-Fi turned off**, next callout still plays | "Everything you see here runs on this laptop. Wi-Fi off, still coaching." | OCR + qwen2.5vl via Ollama; the best playbook was cached before the game | AGI: on-device |
| 0:33–0:53 | Terminal of the evolution loop (proposals scrolling) → the three-arm chart | "Luminal agents propose six playbooks, test them in a simulator, keep the best, then check on held-out seeds they never trained on. With validated memory: **[number from RESULTS.md]**." | `evolve/loop.py`; chart from `scripts/reproduce_chart.py` | Luminal: measurable improvement on fresh tests |
| 0:53–1:08 | Lessons table: planted lesson *"Never buy Juggernog"* turns red, *retired at generation N*; then retrieval side by side: plain vector top 5 vs scored top 5 | "We planted three bad lessons. Validated memory caught and retired them. Naive memory kept them, and its score dropped." | `update_lessons_after_scoring()` transaction; `retrieve()` vs `plain_similar()` | MongoDB: memory that improves itself, tests Xiong et al. 2025 |
| 1:08–1:18 | Claude Code or Cursor connected to the MongoDB MCP server: *"Which situations came right before a down?"* → answer | "Any agent can query the memory in plain English through MongoDB's MCP server." | `mongodb-mcp-server --readOnly` on the same cluster | MongoDB: MCP |
| 1:18–1:30 | README sponsor checklist → `python scripts/reproduce_chart.py` running → RESULTS.md | "Every number here rebuilds from the database with one command. Research and code linked below." | Chart and table regenerated live from `generations` | Credibility |

## Before recording (by 2:10 into the build)

- [ ] At least 8 generations in each of the three arms; `python scripts/reproduce_chart.py` run and the number copied into shot 4 and the README headline
- [ ] Planted lessons were inserted at generation 2 with `planted: true` and `experiment_id`
- [ ] Best playbook marked `is_best: true`; coach cached it at startup
- [ ] Dashboard open on: live state, callout, three-arm chart, lessons table, side-by-side retrieval
- [ ] MCP server connected in Claude Code or Cursor, question tested once
- [ ] 5 minutes of gameplay recorded early (backup if the game site goes down)

## Recording tips

- Record each shot separately (OBS or Windows Game Bar, `Win+Alt+R`) and stitch them. One take of 90 s never works.
- Burn captions into the video; judges may watch muted.
- 1080p, cursor visible, zoom in on small text (the lessons table, the chart numbers).
- Upload to YouTube as **unlisted** and test the link in a private window. Don't submit an MP4.

## If something breaks

| Problem | Do this instead |
| --- | --- |
| Vision model too slow on camera | Show OCR-only readings; say "vision model refreshes every 5 s" |
| Game site down | Use the backup recording; perception runs on recorded frames |
| Arm C doesn't beat B | Show it honestly: "naive memory was as good in this run; here's what the planted lessons did." The planted-lesson shot still works |
| MCP not connecting | Show the same question as a `retrieve()` call in a notebook |
