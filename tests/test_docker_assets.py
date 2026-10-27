"""MVP 0.2.0 P2 Task 8 (design §4.4, S25): the Docker channel — the single-node edition with one boundary in one container,
for trying it out. Checked statically here (the multi-architecture build runs in CI): the Agents never run as root,
there is exactly one boundary, and every pinned version comes from deploy/versions.env, as the release manifest's do."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
BUILD = (ROOT / "deploy/docker/build.sh").read_text()
ENTRY = (ROOT / "deploy/docker/entrypoint.sh").read_text()


def pins():
    return dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


class DockerTests(unittest.TestCase):
    def test_versions_come_from_versions_env_only(self):
        self.assertIn(". deploy/versions.env", BUILD)
        for key, value in pins().items():
            if value not in ("latest", "python3.12"):
                with self.subTest(key):
                    self.assertNotIn(value, DOCKERFILE + BUILD + ENTRY)
        # the image says which release it is, and the build stops when that is not the source's own version
        self.assertIn("ARG VERSION", DOCKERFILE)
        self.assertIn('octopus/__init__.py', DOCKERFILE)
        self.assertIn('org.opencontainers.image.version="${VERSION}"', DOCKERFILE)

    def test_every_download_is_checked(self):
        self.assertIn("node_tarball", BUILD)
        self.assertIn("kiro_package", BUILD)
        self.assertGreaterEqual(BUILD.count("sha256sum -c"), 2)
        self.assertIn('git -C "$SRC" checkout -q "$KIROCREW_GIT_REF"', BUILD)
        for step in ("install_packages", "install_python"):   # the same package lists as install.sh's hosts
            self.assertIn(step, BUILD)

    def test_the_agents_never_run_as_root(self):
        for service in (r"-m composition\.gateway", r"-m octopus [^\n]* worker(\s|&|\)|$)", r"-m octopus [^\n]* serve(\s|&|\)|$)"):
            lines = [line for line in ENTRY.splitlines() if re.search(service, line) and "wait-ready" not in line]
            with self.subTest(service):
                self.assertTrue(lines)
                for line in lines:
                    self.assertRegex(line, r"runuser -u (crew-personal|octopus) --")
        self.assertRegex(ENTRY, r"runuser -u crew-personal -- [^\n]*-m composition\.gateway")
        self.assertRegex(ENTRY, r"runuser -u crew-personal -- [^\n]*-m octopus [^\n]* worker")
        self.assertRegex(ENTRY, r"runuser -u octopus -- [^\n]*-m octopus [^\n]* serve")

    def test_one_boundary_only(self):
        self.assertIn('BOUNDARY=personal', ENTRY)
        self.assertNotIn("boundary-*.toml", ENTRY)
        self.assertNotIn("boundary.sh", ENTRY + DOCKERFILE)
        self.assertNotIn("SINGLE_NODE", ENTRY + BUILD)   # no 「新建边界」 in a container: one person's trial

    def test_the_build_context_leaves_out_local_state(self):
        ignored = (ROOT / ".dockerignore").read_text().split()
        for path in (".git", ".venv", "node_modules", "web/node_modules", "deploy/aws/stack.env", ".worktrees"):
            self.assertIn(path, ignored)

    def test_scripts_are_valid_bash(self):
        for script in ("deploy/docker/build.sh", "deploy/docker/entrypoint.sh"):
            subprocess.run(["bash", "-n", str(ROOT / script)], check=True)

    def test_the_console_listens_where_only_the_host_reaches_it_by_default(self):
        self.assertIn("EXPOSE 8918", DOCKERFILE)
        self.assertIn("127.0.0.1:8918:8918", DOCKERFILE)   # the documented run command publishes it on loopback


if __name__ == "__main__":
    unittest.main()
