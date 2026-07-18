"""Static supply-chain invariants for Issue #274.

Uses only the standard library so CI can run these checks before installing
project dependencies.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[3]


class SupplyChainPinTests(unittest.TestCase):
    def test_external_container_bases_are_tag_and_oci_index_digest_pinned(self):
        expected = {
            "python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93",
            "node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3",
            "nginx:alpine@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752",
        }
        found = set()
        for dockerfile in ROOT.rglob("Dockerfile"):
            for line in dockerfile.read_text().splitlines():
                if line.startswith("FROM "):
                    found.add(line.split()[1])
        self.assertEqual(found, expected)

        production_compose = (ROOT / "docker-compose.prod.yml").read_text()
        self.assertIn(
            "image: caddy:2-alpine@sha256:"
            "5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648",
            production_compose,
        )

    def test_local_build_outputs_remain_tags_not_fabricated_digests(self):
        for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
            compose = (ROOT / compose_name).read_text()
            local_images = re.findall(r"image:\s*(probe-agent/[^\s]+)", compose)
            self.assertTrue(local_images)
            for image in local_images:
                self.assertTrue(image.endswith(":latest"), image)
                self.assertNotIn("@sha256:", image)

    def test_python_locks_pin_every_distribution_with_hashes(self):
        for name in ("requirements.lock", "requirements-dev.lock"):
            path = ROOT / "apps/control-server" / name
            text = path.read_text()
            self.assertIn("--hash=sha256:", text)
            self.assertNotIn("git+", text)
            self.assertNotIn("https://", text)
            blocks = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text)
            requirement_blocks = [
                block for block in blocks
                if re.search(r"(?m)^[A-Za-z0-9_.-]+==[^\s]+", block)
            ]
            self.assertTrue(requirement_blocks)
            for block in requirement_blocks:
                first = re.search(r"(?m)^([A-Za-z0-9_.-]+)==([^\s]+)", block)
                self.assertIsNotNone(first)
                self.assertIn("--hash=sha256:", block, first.group(1))

    def test_docker_and_ci_enforce_locks_without_dependency_reresolution(self):
        dockerfile = (ROOT / "apps/control-server/Dockerfile").read_text()
        self.assertIn("pip install --require-hashes -r requirements.lock", dockerfile)
        self.assertIn("pip install --no-build-isolation --no-deps .", dockerfile)
        self.assertIn(
            "pip install --no-build-isolation --no-deps /opt/probe-agent/packages/python-probe",
            dockerfile,
        )
        self.assertNotIn("pip install --upgrade", dockerfile)

        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("pip install --require-hashes -r apps/control-server/requirements-dev.lock", workflow)
        self.assertIn("pip install --no-build-isolation --no-deps apps/control-server packages/python-probe", workflow)
        self.assertIn("npm ci", workflow)
        self.assertTrue((ROOT / "apps/dashboard/package-lock.json").is_file())

    def test_actions_are_full_sha_pinned_and_spdx_sbom_is_uploaded(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertIn("format: spdx-json", workflow)
        self.assertIn("syft-version: v1.48.0", workflow)
        self.assertIn("name: spdx-sboms", workflow)
        self.assertIn("if-no-files-found: error", workflow)


if __name__ == "__main__":
    unittest.main()
