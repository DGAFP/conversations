#!/usr/bin/env python3
"""Restore the existing password flow only on the verified staging cluster.

No account, password, ministry grant, secret or production resource is changed.
Without --apply this only verifies the target.
"""

import argparse
import datetime
import json
import subprocess
import sys

SERVER = "https://fce3ddcb-b9ff-49fb-8b7d-aca605272b0a.api.k8s.fr-par.scw.cloud:6443"


def run(command):
    # Commands are fixed kubectl operations built below, without a shell.
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=45, check=False
    )  # noqa: S603
    if result.returncode:
        raise RuntimeError("Staging command failed; output hidden")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig]
    config = json.loads(run(kube + ["config", "view", "--minify", "-o", "json"]))
    if config["clusters"][0]["cluster"]["server"] != SERVER:
        raise ValueError("Refusing to change a cluster other than staging")
    target = kube + ["-n", "conversations"]
    release = json.loads(
        run(target + ["get", "helmrelease", "conversations", "-o", "json"])
    )
    if release["spec"].get("suspend"):
        raise ValueError(
            "The staging release is suspended; inspect it before proceeding"
        )
    if args.apply:
        patch = {
            "spec": {
                "values": {
                    "backend": {"envVars": {"ASSISTANT_RH_OIDC_ENABLED": "false"}}
                }
            }
        }
        run(
            target
            + [
                "patch",
                "helmrelease",
                "conversations",
                "--type=merge",
                "-p",
                json.dumps(patch),
            ]
        )
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        run(
            target
            + [
                "annotate",
                "helmrelease",
                "conversations",
                "reconcile.fluxcd.io/requestedAt=" + timestamp,
                "--overwrite",
            ]
        )
    sys.stdout.write(
        json.dumps(
            {
                "staging_verified": True,
                "applied": args.apply,
                "wait_for_flux_ready": args.apply,
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
