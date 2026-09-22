from queue import Queue
from unittest import TestCase
from unittest.mock import Mock
from runtime.presentation import PresentationRouter


class PresentationTests(TestCase):
    def test_failed_normal_start_preserves_pet_consumer(self):
        router = PresentationRouter(Queue())
        pet = Queue()
        router.use_pet(pet)
        process = Mock()
        process.ready_event.wait.return_value = False
        process.is_alive.return_value = True
        router.factory = lambda: process
        with self.assertRaises(RuntimeError):
            router.start_normal()
        process.terminate.assert_called_once()
        self.assertIsNone(router.process)
        router.put({"type": "play_segment"})
        self.assertEqual(pet.get_nowait()["type"], "play_segment")
        self.assertTrue(router.normal_queue.empty())

    def test_switch_stops_old_process_and_replays_only_current_model(self):
        router = PresentationRouter(Queue())
        process = Mock()
        process.ready_event.wait.return_value = True
        process.is_alive.side_effect = [True, False, False]
        router.factory = lambda: process
        router.put({"type": "switch_live2d", "model_json": "model.json"})
        router.start_normal()
        pet = Queue()
        router.use_pet(pet)
        self.assertIsNone(router.process)
        process.join.assert_called_once_with(3)
        router.put({"type": "thinking"})
        self.assertEqual(pet.get_nowait()["model_json"], "model.json")
        self.assertEqual(pet.get_nowait()["type"], "thinking")
        self.assertTrue(pet.empty())
