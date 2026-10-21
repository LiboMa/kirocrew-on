"""D36 §3: the start declaration .octopus/preview.json, and detecting how to start a project without one."""
import json
from pathlib import Path
import tempfile
import unittest

from octopus import preview_spec

FULL = {"processes": [{"name": "backend", "cwd": "backend", "command": "go run .", "port": 8080},
                      {"name": "frontend", "cwd": "frontend", "command": "npm run dev -- --host 127.0.0.1 --port $PORT",
                       "port": "$PORT", "entry": True}],
        "install": [{"cwd": "frontend", "command": "npm ci"}], "ready": {"path": "/", "timeout": 90}}


class PreviewSpecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def declare(self, value):
        (self.project / ".octopus").mkdir(exist_ok=True)
        (self.project / ".octopus/preview.json").write_text(value if isinstance(value, str) else json.dumps(value))

    def test_a_backend_and_frontend_declaration(self):
        self.declare(FULL)
        spec = preview_spec.load(self.project)
        self.assertEqual([p["name"] for p in spec["processes"]], ["backend", "frontend"])
        self.assertEqual(preview_spec.entry(spec)["name"], "frontend")
        self.assertEqual(spec["ready"], {"path": "/", "timeout": 90})
        self.assertEqual(preview_spec.resolve(self.project)[1], "declared")

    def test_a_single_process_is_its_own_entry(self):
        self.declare({"processes": [{"command": "python -m http.server $PORT", "port": "$PORT"}]})
        spec = preview_spec.load(self.project)
        self.assertEqual(preview_spec.entry(spec)["command"], "python -m http.server $PORT")
        self.assertEqual(spec["ready"]["timeout"], 120)

    def test_invalid_declarations_say_what_is_wrong(self):
        two = {"processes": [{"command": "a", "port": 3000, "entry": True}, {"command": "b", "port": 3001, "entry": True}]}
        cases = {"no processes": {"processes": []}, "two entries": two,
                 "outside": {"processes": [{"command": "x", "port": 3000, "cwd": "../other"}]},
                 "absolute": {"processes": [{"command": "x", "port": 3000, "cwd": "/etc"}]},
                 "low port": {"processes": [{"command": "x", "port": 80}]},
                 "bad port": {"processes": [{"command": "x", "port": "abc"}]},
                 "empty command": {"processes": [{"command": " ", "port": 3000}]},
                 "too many": {"processes": [{"command": "x", "port": 3000 + i} for i in range(5)]},
                 "not json": "{processes"}
        for name, value in cases.items():
            with self.subTest(name=name):
                self.declare(value)
                with self.assertRaises(ValueError):
                    preview_spec.load(self.project)
                spec, source, error = preview_spec.resolve(self.project)
                self.assertEqual((spec, source), (None, None))
                self.assertIn("preview.json", error)

    def test_detection_without_a_declaration(self):
        package = lambda scripts: (self.project / "package.json").write_text(json.dumps({"scripts": scripts}))
        package({"dev": "vite", "build": "vite build"})
        self.assertEqual(preview_spec.detect(self.project)["install"], [{"cwd": ".", "command": "npm install"}])
        (self.project / "package-lock.json").write_text("{}")   # npm ci needs the lockfile
        vite = preview_spec.detect(self.project)
        self.assertEqual(vite["install"], [{"cwd": ".", "command": "npm ci"}])
        self.assertEqual(vite["processes"][0]["command"], "npm run dev -- --host 127.0.0.1 --port $PORT --strictPort")
        (self.project / "node_modules").mkdir()
        self.assertEqual(preview_spec.detect(self.project)["install"], [])
        package({"dev": "next dev"})
        self.assertEqual(preview_spec.detect(self.project)["processes"][0]["command"], "npm run dev")
        package({"start": "node server.js"})
        self.assertEqual(preview_spec.detect(self.project)["processes"][0]["command"], "npm start")
        (self.project / "package.json").unlink()
        self.assertIsNone(preview_spec.detect(self.project))
        (self.project / "index.html").write_text("<h1>hi</h1>")
        self.assertEqual(preview_spec.detect(self.project), {"static": ".", "processes": [], "install": [],
                                                            "ready": {"path": "/", "timeout": 120}})
        self.assertEqual(preview_spec.resolve(self.project)[1], "detected")

    def test_a_packaged_project_installs_what_the_package_leaves_out(self):
        (self.project / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
        (self.project / "package-lock.json").write_text("{}")
        (self.project / "node_modules").mkdir()
        self.assertEqual(preview_spec.detect(self.project)["install"], [])
        self.assertEqual(preview_spec.detect(self.project, packaged=True)["install"], [{"cwd": ".", "command": "npm ci"}])
        self.assertEqual(preview_spec.resolve(self.project, packaged=True)[0]["install"][0]["command"], "npm ci")

    def test_an_unusable_package_json_is_not_an_error(self):
        for scripts in (["dev"], "dev", 3):
            (self.project / "package.json").write_text(json.dumps({"scripts": scripts}))
            self.assertIsNone(preview_spec.detect(self.project))
        (self.project / "package.json").write_text(json.dumps(["not", "an", "object"]))
        self.assertIsNone(preview_spec.detect(self.project))

    def test_local_steps(self):
        self.declare(FULL)
        steps = preview_spec.local_steps(preview_spec.load(self.project))
        self.assertEqual(steps, ["cd frontend && npm ci", "cd backend && go run .",
                                 "cd frontend && npm run dev -- --host 127.0.0.1 --port 5173",
                                 "打开 http://localhost:5173/"])
        static = preview_spec.local_steps({"static": ".", "processes": [], "install": [], "ready": {"path": "/"}})
        self.assertEqual(static, ["python3 -m http.server 8000", "打开 http://localhost:8000/"])


    def test_a_cwd_is_quoted_in_the_local_steps_and_control_characters_are_refused(self):
        self.declare({"processes": [{"cwd": "my app", "command": "npm run dev", "port": "$PORT"}],
                      "install": [{"cwd": "$(touch pwned)", "command": "npm ci"}]})
        steps = preview_spec.local_steps(preview_spec.load(self.project))
        self.assertEqual(steps[:2], ["cd '$(touch pwned)' && npm ci", "cd 'my app' && npm run dev"])
        self.declare({"processes": [{"cwd": "app\nrm -rf ~", "command": "npm run dev", "port": "$PORT"}]})
        with self.assertRaises(ValueError):
            preview_spec.load(self.project)

    def test_a_symlinked_declaration_is_not_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "elsewhere.json"
        target.write_text(json.dumps(FULL))
        (self.project / ".octopus").mkdir()
        (self.project / ".octopus/preview.json").symlink_to(target)
        with self.assertRaises(ValueError):
            preview_spec.load(self.project)

if __name__ == "__main__":
    unittest.main()
