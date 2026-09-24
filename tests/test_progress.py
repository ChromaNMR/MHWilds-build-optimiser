"""Progress reporting and cancellation of a run."""

from __future__ import annotations

import queue
import threading
import time
import types
import unittest

from tests.support import game, import_gui, weighted

from optimiser import SearchCancelled, Scoring, optimise

EASY = {"Antivirus": (5, 0), "Weakness Exploit": (5, 0)}


class Progress(unittest.TestCase):
    def test_reports_rise_from_search_to_evaluation(self):
        seen: list[tuple[float, str]] = []
        optimise(game(), Scoring(weighted(EASY)), progress=lambda f, m: seen.append((f, m)))
        fractions = [f for f, _m in seen]
        self.assertTrue(seen)
        self.assertEqual(fractions, sorted(fractions))
        self.assertTrue(all(0.0 <= f <= 1.0 for f in fractions))
        messages = {m.split(":")[0] for _f, m in seen}
        self.assertEqual(messages, {"Searching armour", "Evaluating sets"})

    def test_no_callbacks_is_the_default(self):
        sets, _, _ = optimise(game(), Scoring(weighted(EASY)))
        self.assertEqual(len(sets), 10)


class Cancel(unittest.TestCase):
    def test_should_stop_raises(self):
        with self.assertRaises(SearchCancelled):
            optimise(game(), Scoring(weighted(EASY)), should_stop=lambda: True)

    def test_cancel_mid_evaluation(self):
        # Stop at the first evaluation report: proves the check sits inside
        # the long loop, not only at the start.
        state = {"evaluating": False}

        def progress(_fraction, message):
            if message.startswith("Evaluating"):
                state["evaluating"] = True

        with self.assertRaises(SearchCancelled):
            optimise(
                game(),
                Scoring(weighted(EASY)),
                progress=progress,
                should_stop=lambda: state["evaluating"],
            )

    def test_cancel_answers_quickly(self):
        event = threading.Event()
        errors: list[BaseException] = []

        def work():
            try:
                optimise(game(), Scoring(weighted(EASY)), should_stop=event.is_set)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(0.3)
        started = time.monotonic()
        event.set()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual([type(e) for e in errors], [SearchCancelled])


class GuiWorker(unittest.TestCase):
    def test_worker_posts_progress_then_cancelled(self):
        G = import_gui()
        stub = types.SimpleNamespace(game_data=game())
        results: queue.Queue = queue.Queue()
        event = threading.Event()
        event.set()
        G.SkillsGui._optimiser_thread(
            stub, G.RunRequest(skills=weighted(EASY)), results, event
        )
        kinds = []
        while not results.empty():
            kinds.append(results.get_nowait()[0])
        self.assertEqual(kinds[-1], "cancelled")

    def test_worker_posts_progress_then_ok(self):
        G = import_gui()
        stub = types.SimpleNamespace(game_data=game())
        results: queue.Queue = queue.Queue()
        G.SkillsGui._optimiser_thread(
            stub, G.RunRequest(skills=weighted(EASY)), results, threading.Event()
        )
        kinds = []
        while not results.empty():
            kinds.append(results.get_nowait()[0])
        self.assertIn("progress", kinds)
        self.assertEqual(kinds[-1], "ok")
        self.assertEqual(kinds.count("ok"), 1)


class GuiPoll(unittest.TestCase):
    """_poll_optimiser_queue against a queue filled by hand."""

    def make_stub(self, *messages):
        from unittest import mock

        from tests.support import Value

        G = import_gui()
        stub = types.SimpleNamespace(
            _optimiser_queue=queue.Queue(),
            root=mock.MagicMock(),
            cancel_button=mock.MagicMock(),
            run_button=mock.MagicMock(),
            progress_var=Value(),
            progress_text_var=Value(),
            status_var=Value(),
            _optimiser_running=True,
            _optimiser_done=mock.MagicMock(),
            _poll_optimiser_queue=mock.MagicMock(),  # what after() reschedules
        )
        for message in messages:
            stub._optimiser_queue.put(message)
        G.SkillsGui._poll_optimiser_queue(stub)
        return stub

    def test_progress_only_keeps_polling(self):
        stub = self.make_stub(("progress", 0.1, "a", None), ("progress", 0.4, "b", None))
        self.assertEqual((stub.progress_var.get(), stub.progress_text_var.get()), (0.4, "b"))
        stub.root.after.assert_called_once()
        stub._optimiser_done.assert_not_called()

    def test_result_after_progress_finishes(self):
        stub = self.make_stub(("progress", 0.9, "x", None), ("ok", ["set"], "scoring", []))
        stub._optimiser_done.assert_called_once_with(["set"], "scoring", None, [])
        stub.root.after.assert_not_called()

    def test_cancelled_resets_quietly(self):
        stub = self.make_stub(("cancelled", None, None, None))
        self.assertEqual(stub.status_var.get(), "Cancelled.")
        self.assertFalse(stub._optimiser_running)
        stub._optimiser_done.assert_not_called()


if __name__ == "__main__":
    unittest.main()
