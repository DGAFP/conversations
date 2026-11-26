#!/usr/bin/env python3
"""Synchroniser la clé Scaleway TEM staging depuis Secret Manager, sans fichier de secrets.

Lecture seule par défaut ; --apply écrit uniquement le Secret Kubernetes conversations-tem.
Ne pas lancer avec un outil de trace des entrées/sorties de sous-processus.
"""

import argparse
import base64
import json
import subprocess
import sys

PROJECT = "c1d3f629-d08d-439d-ba76-f115ea06e579"
REGION = "fr-par"
SECRET_PATH = "/assistant-rh/staging/runtime"
SERVER = "https://fce3ddcb-b9ff-49fb-8b7d-aca605272b0a.api.k8s.fr-par.scw.cloud:6443"
SECRETS = {
    "SCW_TEM_SECRET_KEY": "conversations-tem-secret-key",
}


def run(command, data=None):
    """Execute a subprocess without exposing secret material on failure."""
    result = subprocess.run(command, input=data, capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise RuntimeError("Commande en échec ; sortie masquée pour protéger les identifiants")
    return json.loads(result.stdout)


def resolve_secret_id(name):
    """Find the unique Scaleway Secret Manager secret matching project, path, and name."""
    items = run([
        "scw", "secret", "secret", "list",
        f"project-id={PROJECT}", f"path={SECRET_PATH}", f"name={name}",
        f"region={REGION}", "-o", "json",
    ])
    matches = [
        item for item in items
        if (item.get("project_id"), item.get("path"), item.get("name"))
        == (PROJECT, SECRET_PATH, name)
    ]
    if len(matches) != 1:
        raise ValueError(f"Secret unique introuvable dans Secret Manager pour {name}")
    return matches[0]["id"]


def read_latest_secret(name):
    """Read and validate the latest enabled revision of a secret."""
    identifier = resolve_secret_id(name)
    versions = run(["scw", "secret", "version", "list", identifier, f"region={REGION}", "-o", "json"])
    enabled = [version["revision"] for version in versions if version.get("status") == "enabled"]
    if not enabled:
        raise ValueError(f"Aucune révision active dans Secret Manager pour {name}")
    revision = max(enabled)
    version = run([
        "scw", "secret", "version", "access", identifier,
        f"revision={revision}", f"region={REGION}", "-o", "json",
    ])
    value = base64.b64decode(version["data"], validate=True).decode("utf-8")
    if not value or value != value.strip() or any(ch.isspace() for ch in value):
        raise ValueError("Identifiant vide ou mal formé ; corriger Secret Manager")
    return value, revision


def sync(kubeconfig, apply=False):
    """Verify target cluster, read Scaleway TEM secret(s), and optionally apply K8s Secret."""
    kube = ["kubectl", "--kubeconfig", kubeconfig]
    config = run(kube + ["config", "view", "--minify", "-o", "json"])
    if config["clusters"][0]["cluster"]["server"] != SERVER:
        raise ValueError("Cluster différent du staging autorisé")

    values, revisions = {}, {}
    for variable, name in SECRETS.items():
        value, revision = read_latest_secret(name)
        values[variable] = value
        revisions[name] = revision

    if apply:
        resource = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {
                "namespace": "conversations",
                "name": "conversations-tem",
                "annotations": {"assistant-rh/tem-secret-revisions": json.dumps(revisions)},
            },
            "stringData": values,
        }
        # stdin + server-side apply : pas de fichier, ni d'annotation last-applied avec les valeurs.
        run(
            kube + ["apply", "--server-side", "--field-manager=tem-staging", "-f", "-", "-o", "json"],
            json.dumps(resource).encode("utf-8"),
        )
    return {"staging_verified": True, "revisions": revisions, "applied": apply}


def main():
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = sync(args.kubeconfig, apply=args.apply)
    sys.stdout.write(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
