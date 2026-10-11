"""Failure-path checks with no browser, GPU, input devices, or MongoDB connection."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkerRecoveryTests(TestCase):
    def setUp(self):
        self.keyboard = Mock()
        self.capture = Mock()
        self.recorder = Mock()
        # Keep the real controller and worker logic; replace platform/hardware dependencies.
        self.modules = {
            "cv2": Mock(), "numpy": Mock(), "keyboard": self.keyboard,
            "vision.capture": SimpleNamespace(Capture=self.capture),
            "vision.run": SimpleNamespace(Recorder=self.recorder),
            "bot.game": SimpleNamespace(PortGame=Mock()),
        }
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, self.modules).start()
        self.controller = load_module("recovery_controller", "bot/controller.py")
        patch.dict(sys.modules, {"bot.controller": self.controller}).start()
        self.worker = load_module("recovery_worker", "bot/worker.py")
        self.worker.log = Mock()
        self.sleep = patch.object(self.worker, "time", SimpleNamespace(sleep=Mock())).start().sleep

    def assert_episode_cleanup(self, game, *, supplied_capture=False):
        cap = Mock() if supplied_capture else None
        with self.assertRaises(self.controller.InvalidEpisode):
            self.controller.run_episode(game, Mock(), Mock(), {}, cap=cap, record_path="unused.mp4")
        self.keyboard.remove_hotkey.assert_called_once_with(self.keyboard.add_hotkey.return_value)
        game.keys.return_value.release_all.assert_called()
        self.recorder.return_value.close.assert_called_once()
        if cap is None:
            self.capture.return_value.close.assert_called_once()
        else:
            cap.close.assert_not_called()  # Runner owns this capture and reuses it on retry.
        self.capture.return_value.grab.assert_not_called()

    def test_initial_control_failure_cleans_up_owned_resources(self):
        game = Mock()
        game.ensure_locked.return_value = False
        self.assert_episode_cleanup(game)

    def test_initial_control_failure_preserves_runner_capture(self):
        game = Mock()
        game.ensure_locked.return_value = False
        self.assert_episode_cleanup(game, supplied_capture=True)

    def test_closed_browser_cleans_up_without_scoring(self):
        game = Mock()
        game.ensure_locked.return_value = True
        game.locked.return_value = False
        game.page.is_closed.return_value = True
        self.assert_episode_cleanup(game)

    def test_early_control_loss_cleans_up_without_scoring(self):
        game = Mock()
        game.ensure_locked.side_effect = [True, False]
        game.locked.return_value = False
        game.page.is_closed.return_value = False
        with patch.object(self.controller, "time", SimpleNamespace(time=lambda: 100.0)):
            self.assert_episode_cleanup(game)

    def setup_queue(self, outcomes, release=True):
        orch = Mock()
        orch.db = {"experiments": Mock()}
        orch.db["experiments"].find_one.return_value = {"map": "kino"}
        job = {"job_id": "job-1", "strategy_id": "baseline", "kind": "eval"}
        orch.claim_job.side_effect = [dict(job) for _ in outcomes] + [None]
        orch.release_job.return_value = release
        orch.status.return_value = {"generation": 1, "jobs": {"done": 1}}
        runner = Mock()
        runner.run.side_effect = outcomes
        patch.object(self.worker, "Runner", return_value=runner).start()
        patch.dict(sys.modules, {"mongo_orchestrator": SimpleNamespace(MongoOrchestrator=lambda _: orch)}).start()
        args = SimpleNamespace(experiment="test", map="kino", worker="test-worker", generations=1,
                               propose=False, wait_s=0, config="unused", out_dir=None, save_frames=None,
                               frame_every=1, max_s=180, weights=None, live_view=False, slot=0)
        return orch, runner, args

    def test_invalid_game_requeues_then_success_is_the_only_score(self):
        result = {"job_id": "job-1", "points": 100}
        orch, runner, args = self.setup_queue([self.controller.InvalidEpisode("lost control"), result])
        self.worker.run_mongo(args)
        orch.release_job.assert_called_once_with("job-1", "test-worker")
        orch.complete_job.assert_called_once_with(result)
        self.sleep.assert_called_once()
        runner.close.assert_called_once()

    def test_repeated_invalid_games_stop_with_job_requeued(self):
        orch, runner, args = self.setup_queue([self.controller.InvalidEpisode("lost control") for _ in range(3)])
        with self.assertRaisesRegex(RuntimeError, "three consecutive"):
            self.worker.run_mongo(args)
        self.assertEqual(orch.release_job.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        orch.complete_job.assert_not_called()
        runner.close.assert_called_once()

    def test_success_resets_consecutive_failure_count(self):
        invalid = self.controller.InvalidEpisode
        result = {"job_id": "job-1", "points": 100}
        orch, runner, args = self.setup_queue([invalid(), invalid(), result, invalid(), invalid(), result])
        self.worker.run_mongo(args)
        self.assertEqual(orch.complete_job.call_count, 2)
        self.assertEqual(orch.release_job.call_count, 4)
        runner.close.assert_called_once()

    def test_lost_job_ownership_stops_retry(self):
        orch, runner, args = self.setup_queue([self.controller.InvalidEpisode()], release=False)
        with self.assertRaisesRegex(RuntimeError, "no longer owns"):
            self.worker.run_mongo(args)
        self.sleep.assert_not_called()
        orch.complete_job.assert_not_called()
        runner.close.assert_called_once()

    def test_unexpected_failure_requeues_and_propagates(self):
        orch, runner, args = self.setup_queue([ValueError("broken detector")])
        with self.assertRaisesRegex(ValueError, "broken detector"):
            self.worker.run_mongo(args)
        orch.release_job.assert_called_once_with("job-1", "test-worker")
        orch.complete_job.assert_not_called()
        runner.close.assert_called_once()

    def test_f8_still_requeues_without_retry(self):
        orch, runner, args = self.setup_queue([None])
        self.worker.run_mongo(args)
        orch.release_job.assert_called_once_with("job-1", "test-worker")
        orch.complete_job.assert_not_called()
        self.sleep.assert_not_called()
        runner.close.assert_called_once()
