#!/usr/bin/env python3
"""Synchroniser les deux identifiants ProConnect staging, sans fichier de secrets.

Lecture seule par défaut ; --apply écrit uniquement conversations-proconnect.
Ne pas lancer avec un outil de trace des entrées/sorties de sous-processus.
"""

import argparse
import base64
import json
import subprocess
import sys

PROJECT = "c1d3f629-d08d-439d-ba76-f115ea06e579"
SERVER = "https://fce3ddcb-b9ff-49fb-8b7d-aca605272b0a.api.k8s.fr-par.scw.cloud:6443"
SECRETS = {
    "OIDC_RP_CLIENT_ID": ("conversations-proconnect-client-id", "329357f9-b09c-4dcf-8cc2-0720f373e0f7"),
    "OIDC_RP_CLIENT_SECRET": ("conversations-proconnect-client-secret", "1fb76d32-106e-4035-9829-85c2ae35fc24"),
}


def run(command, data=None):
    result = subprocess.run(command, input=data, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Commande en échec ; sortie masquée pour protéger les identifiants")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig]
    config = run(kube + ["config", "view", "--minify", "-o", "json"])
    if config["clusters"][0]["cluster"]["server"] != SERVER:
        raise ValueError("Cluster différent du staging autorisé")
    values, revisions = {}, {}
    for variable, (name, identifier) in SECRETS.items():
        metadata = run(["scw", "secret", "secret", "get", identifier, "region=fr-par", "-o", "json"])
        if (metadata["project_id"], metadata["path"], metadata["name"]) != (
            PROJECT, "/assistant-rh/staging/runtime", name
        ):
            raise ValueError("Secret hors du périmètre staging attendu")
        versions = run(["scw", "secret", "version", "list", identifier, "region=fr-par", "-o", "json"])
        revision = max(version["revision"] for version in versions if version["status"] == "enabled")
        version = run([
            "scw", "secret", "version", "access", identifier,
            f"revision={revision}", "region=fr-par", "-o", "json",
        ])
        value = base64.b64decode(version["data"], validate=True).decode("utf-8")
        if not value or value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("Identifiant vide ou mal formé ; corriger Secret Manager")
        values[variable] = value
        revisions[name] = revision
    if args.apply:
        resource = {
            "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {
                "namespace": "conversations", "name": "conversations-proconnect",
                "annotations": {"assistant-rh/proconnect-secret-revisions": json.dumps(revisions)},
            },
            "stringData": values,
        }
        # stdin + server-side apply : pas de fichier, ni d'annotation last-applied contenant les valeurs.
        run(kube + ["apply", "--server-side", "--field-manager=proconnect-staging", "-f", "-", "-o", "json"], json.dumps(resource).encode())
    sys.stdout.write(json.dumps({"staging_verified": True, "revisions": revisions, "applied": args.apply}) + "\n")


if __name__ == "__main__":
    main()
