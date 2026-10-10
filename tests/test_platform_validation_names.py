import copy
import tempfile
from pathlib import Path
import unittest

from builder.platform import PlatformError
from test_platform import MemoryPlatform, definition


class UniqueNamePlatform(MemoryPlatform):
    """The live API enforces unique names even when dryRun=true."""
    def __init__(self):
        super().__init__()
        self.saved_names = set()
        self.validation_names = []

    def request(self, method, path, body=None, params=None):
        if method == "POST" and path == "/api/v2/workflow":
            if body["name"] in self.saved_names:
                raise PlatformError("A workflow with this name already exists")
            if params == {"dryRun": "true"}:
                self.validation_names.append(body["name"])
            else:
                self.saved_names.add(body["name"])
        return super().request(method, path, body, params)


class ValidationNameTests(unittest.TestCase):
    def test_revalidation_after_save_avoids_name_collision_and_keeps_slot(self):
        client = UniqueNamePlatform()
        authored = definition()
        original = copy.deepcopy(authored)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "slots.json"
            client.validate_workflow("stable-slot", authored)
            first = client.save_workflow("stable-slot", authored, state)
            changed = copy.deepcopy(authored)
            changed["nodes"]["greeting"]["task_message"] = "Updated harmless task"
            client.validate_workflow("stable-slot", changed)
            second = client.save_workflow("stable-slot", changed, state)
        self.assertEqual(authored, original)
        self.assertEqual(first["workflow_id"], second["workflow_id"])
        self.assertGreater(second["version_number"], first["version_number"])
        self.assertEqual(client.saved_names, {"stable-slot"})
        self.assertEqual(len(set(client.validation_names)), 2)
        self.assertTrue(all(name.startswith("builder-validation-") for name in client.validation_names))
        self.assertNotIn("stable-slot", client.validation_names)


if __name__ == "__main__":
    unittest.main()
