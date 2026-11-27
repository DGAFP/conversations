"""Build and inspect a real Helm bundle; no registry or cluster access."""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPOSITORY = "DGAFP/conversations"
RELEASE = "conversations"
SCRIPT = Path(__file__).with_name("package-staging.py").resolve()
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64


class PackageTest(unittest.TestCase):
    def environment(self, directory):
        return dict(
            os.environ,
            GITHUB_REPOSITORY=REPOSITORY,
            GITHUB_REF_NAME="main",
            RELEASE_TAG="v0.1.0-rc.1",
            RELEASE_ENVIRONMENT="staging",
            SOURCE_SHA=SHA,
            CHART_VERSION="0.74.0",
            RELEASE_REGISTRY="rg.fr-par.scw.cloud/assistant-rh-staging",
            BUNDLE_OUTPUT_DIR=directory,
            GITHUB_OUTPUT=directory + "/outputs",
            IMAGE_DIGEST=DIGEST,
            BACKEND_DIGEST=DIGEST,
            FRONTEND_DIGEST=DIGEST,
        )

    def test_real_bundle_contains_pinned_images_and_health_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["python3", str(SCRIPT)],
                env=self.environment(directory),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            archive = Path(directory) / (RELEASE + "-0.74.0.tgz")
            with tarfile.open(archive) as package:
                values = json.load(package.extractfile(RELEASE + "/values.yaml"))
                hook = (
                    package.extractfile(RELEASE + "/templates/staging-smoke.yaml").read().decode()
                )
                self.assertIn("helm.sh/hook: test", hook)
                self.assertNotIn(RELEASE + "/templates/bundle-values.yaml", package.getnames())
            if RELEASE == "assistant-rh-api":
                self.assertEqual(values["image"]["digest"], DIGEST)
            else:
                self.assertEqual(values["image"]["tag"], "sha-" + SHA + "@" + DIGEST)
                self.assertEqual(values["frontend"]["image"]["tag"], "sha-" + SHA + "@" + DIGEST)
                env = values["backend"]["envVars"]
                self.assertEqual(env["LOGIN_REDIRECT_URL"], "/")
                self.assertEqual(env["LOGOUT_REDIRECT_URL"], "/")
                self.assertEqual(env["FEATURE_FLAG_DOCUMENT_UPLOAD"], "DISABLED")
                self.assertFalse(values["backend"]["celeryBeat"]["enabled"])
                workers = values["backend"]["celeryWorkers"]
                self.assertEqual(len(workers), 1)
                self.assertEqual(workers[0]["replicas"], 1)
                self.assertIn("--concurrency=1", workers[0]["args"])
                self.assertIn("--check", str(values["backend"]["migrate"]))
            rendered = subprocess.run(
                ["helm", "template", RELEASE, str(archive), "-n", RELEASE],
                capture_output=True,
                text=True,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn(DIGEST, rendered.stdout)
            if RELEASE == "assistant-rh-api":
                self.assertNotIn("kind: Ingress\n", rendered.stdout)
            else:
                host = "staging.assistant-rh.beta.gouv.fr"
                self.assertIn(host, env["DJANGO_ALLOWED_HOSTS"].split(","))
                self.assertEqual(env["DJANGO_CSRF_TRUSTED_ORIGINS"], "https://" + host)
                ingress = values["ingress"]
                self.assertTrue(ingress["enabled"])
                self.assertEqual(ingress["host"], host)
                self.assertEqual(ingress["className"], "traefik")
                self.assertEqual(
                    ingress["annotations"]["traefik.ingress.kubernetes.io/router.entrypoints"],
                    "websecure",
                )
                self.assertEqual(
                    ingress["annotations"]["traefik.ingress.kubernetes.io/router.tls"], "true"
                )
                self.assertEqual(
                    ingress["annotations"]["traefik.ingress.kubernetes.io/router.tls.certresolver"],
                    "letsencrypt",
                )
                self.assertFalse(ingress["tls"]["enabled"])
                self.assertFalse(values["ingressAdmin"]["enabled"])
                self.assertFalse(values["ingressMedia"]["enabled"])
                self.assertEqual(rendered.stdout.count("kind: Ingress\n"), 1)
                self.assertIn(host, rendered.stdout)

    def test_bad_identity_or_digest_cannot_produce_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, value in [
                ("GITHUB_REPOSITORY", "other/project"),
                ("RELEASE_TAG", "v0.1.0-rc.0"),
                ("SOURCE_SHA", "invalid"),
                ("CHART_VERSION", "0"),
                ("RELEASE_ENVIRONMENT", "production"),
                ("IMAGE_DIGEST" if RELEASE == "assistant-rh-api" else "BACKEND_DIGEST", "latest"),
            ]:
                with self.subTest(key=key):
                    env = self.environment(directory)
                    env[key] = value
                    result = subprocess.run(["python3", str(SCRIPT)], env=env, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(list(Path(directory).glob("*.tgz")), [])

    def test_production_uses_reviewed_values_and_the_same_image_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            shutil.copytree(
                SCRIPT.parents[2] / "src/helm/conversations", source / "src/helm/conversations"
            )
            values = source / "deploy/kapsule/production.yaml"
            values.parent.mkdir(parents=True)
            values.write_text(
                "backend:\n  envVars:\n    ENVIRONMENT: production\ningress:\n  enabled: false\n  host: assistant-rh.example.invalid\n"
            )
            env = self.environment(str(root / "output"))
            env.update(
                SOURCE_ROOT=str(source),
                RELEASE_TAG="v0.1.0",
                RELEASE_ENVIRONMENT="production",
                RELEASE_REGISTRY="rg.fr-par.scw.cloud/assistant-rh-production",
            )
            result = subprocess.run(
                ["python3", str(SCRIPT)], env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with tarfile.open(root / "output/conversations-0.74.0.tgz") as archive:
                sealed = json.load(archive.extractfile("conversations/values.yaml"))
            self.assertEqual(sealed["backend"]["envVars"]["ENVIRONMENT"], "production")
            self.assertEqual(sealed["backend"]["image"]["tag"], "sha-" + SHA + "@" + DIGEST)
            self.assertEqual(sealed["frontend"]["image"]["tag"], "sha-" + SHA + "@" + DIGEST)
            self.assertIn("assistant-rh-production", sealed["backend"]["image"]["repository"])
            values.write_text(
                values.read_text().replace(
                    "assistant-rh.example.invalid", "staging.example.invalid"
                )
            )
            env["BUNDLE_OUTPUT_DIR"] = str(root / "rejected")
            result = subprocess.run(
                ["python3", str(SCRIPT)], env=env, capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list((root / "rejected").glob("*.tgz")), [])


if __name__ == "__main__":
    unittest.main()
