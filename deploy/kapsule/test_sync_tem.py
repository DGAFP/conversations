"""Unit tests for deploy/kapsule/sync-tem.py without cluster or Scaleway access."""

import base64
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

SCRIPT_PATH = Path(__file__).with_name("sync-tem.py").resolve()
SPEC = importlib.util.spec_from_file_location("sync_tem", SCRIPT_PATH)
sync_tem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_tem)


def _fake_scw_and_kubectl(secret_value="00000000-0000-0000-0000-000000000001", server=None):
    """Build a fake run() implementation recording kubectl apply payloads."""
    applied = []
    target_server = server or sync_tem.SERVER

    def fake_run(command, data=None):
        if command[:4] == ["kubectl", "--kubeconfig", "/tmp/kc", "config"]:
            return {"clusters": [{"cluster": {"server": target_server}}]}
        if command[:4] == ["scw", "secret", "secret", "list"]:
            return [{
                "id": "secret-uuid-1",
                "project_id": sync_tem.PROJECT,
                "path": sync_tem.SECRET_PATH,
                "name": "conversations-tem-secret-key",
            }]
        if command[:4] == ["scw", "secret", "version", "list"]:
            return [
                {"revision": 1, "status": "disabled"},
                {"revision": 2, "status": "enabled"},
            ]
        if command[:4] == ["scw", "secret", "version", "access"]:
            encoded = base64.b64encode(secret_value.encode("utf-8")).decode("ascii")
            return {"data": encoded}
        if "apply" in command:
            applied.append((command, json.loads(data.decode("utf-8"))))
            return {"metadata": {"name": "conversations-tem"}}
        raise AssertionError(f"Unexpected command: {command}")

    return fake_run, applied


class SyncTEMTest(unittest.TestCase):
    """Verify scope checks, dry-run behavior, and Kubernetes Secret output."""

    def test_dry_run_verifies_without_applying(self):
        fake_run, applied = _fake_scw_and_kubectl()
        with mock.patch.object(sync_tem, "run", side_effect=fake_run):
            result = sync_tem.sync("/tmp/kc", apply=False)
        self.assertEqual(
            result,
            {
                "staging_verified": True,
                "revisions": {"conversations-tem-secret-key": 2},
                "applied": False,
            },
        )
        self.assertEqual(applied, [])

    def test_apply_writes_expected_kubernetes_secret(self):
        secret_value = "11111111-2222-3333-4444-555555555555"
        fake_run, applied = _fake_scw_and_kubectl(secret_value=secret_value)
        with mock.patch.object(sync_tem, "run", side_effect=fake_run):
            result = sync_tem.sync("/tmp/kc", apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual(len(applied), 1)
        command, manifest = applied[0]
        self.assertIn("--server-side", command)
        self.assertEqual(manifest["metadata"]["namespace"], "conversations")
        self.assertEqual(manifest["metadata"]["name"], "conversations-tem")
        self.assertEqual(manifest["stringData"], {"SCW_TEM_SECRET_KEY": secret_value})
        self.assertEqual(
            json.loads(manifest["metadata"]["annotations"]["assistant-rh/tem-secret-revisions"]),
            {"conversations-tem-secret-key": 2},
        )

    def test_rejects_wrong_cluster_before_reading_secrets(self):
        fake_run, applied = _fake_scw_and_kubectl(server="https://other.k8s.example:6443")
        with mock.patch.object(sync_tem, "run", side_effect=fake_run):
            with self.assertRaises(ValueError):
                sync_tem.sync("/tmp/kc", apply=True)
        self.assertEqual(applied, [])

    def test_rejects_whitespace_in_secret_value(self):
        fake_run, applied = _fake_scw_and_kubectl(secret_value="bad-secret\n")
        with mock.patch.object(sync_tem, "run", side_effect=fake_run):
            with self.assertRaises(ValueError):
                sync_tem.sync("/tmp/kc", apply=True)
        self.assertEqual(applied, [])


if __name__ == "__main__":
    unittest.main()
