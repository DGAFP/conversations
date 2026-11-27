#!/usr/bin/env python3
"""Seal reviewed environment values and immutable image digests into a Helm chart."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from release import REPOSITORY, STAGING_REGISTRY, classify


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise RuntimeError("Unexpected delivery repository")
    tag = os.environ["RELEASE_TAG"]
    _, target = classify(tag)
    if target != os.environ["RELEASE_ENVIRONMENT"]:
        raise RuntimeError("Tag does not select this environment")
    sha = os.environ["SOURCE_SHA"]
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise RuntimeError("Invalid source commit")
    version = os.environ["CHART_VERSION"]
    if not re.fullmatch(r"0\.[1-9][0-9]*\.0", version):
        raise RuntimeError("Invalid chart sequence")
    registry = os.environ["RELEASE_REGISTRY"]
    if not re.fullmatch(r"rg\.fr-par\.scw\.cloud/[a-z0-9-]+", registry):
        raise RuntimeError("Invalid registry")
    if (target == "staging") != (registry == STAGING_REGISTRY):
        raise RuntimeError("Registry does not match the target environment")
    for key in ("BACKEND_DIGEST", "FRONTEND_DIGEST"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", os.environ[key]):
            raise RuntimeError("Invalid image digest")
    source = Path(os.environ.get("SOURCE_ROOT", ".")).resolve()
    overrides = source / "deploy/kapsule" / (target + ".yaml")
    if not overrides.is_file():
        raise RuntimeError("Reviewed environment values are missing")
    destination = Path(os.environ["BUNDLE_OUTPUT_DIR"]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        chart = Path(temporary) / "conversations"
        shutil.copytree(source / "src/helm/conversations", chart)
        template = chart / "templates/bundle-values.yaml"
        template.write_text("{{ .Values | toJson }}\n")
        args = [
            "helm",
            "template",
            "conversations",
            str(chart),
            "--namespace",
            "conversations",
            "--show-only",
            "templates/bundle-values.yaml",
            "-f",
            str(overrides),
            "--set-string",
            "image.repository=" + registry + "/conversations-backend",
            "--set-string",
            "backend.image.repository=" + registry + "/conversations-backend",
            "--set-string",
            "frontend.image.repository=" + registry + "/conversations-frontend",
            "--set-string",
            "image.tag=sha-" + sha + "@" + os.environ["BACKEND_DIGEST"],
            "--set-string",
            "backend.image.tag=sha-" + sha + "@" + os.environ["BACKEND_DIGEST"],
            "--set-string",
            "frontend.image.tag=sha-" + sha + "@" + os.environ["FRONTEND_DIGEST"],
        ]
        rendered = subprocess.check_output(args, text=True)
        values = json.loads(
            "\n".join(
                line
                for line in rendered.splitlines()
                if line.strip() and not line.startswith(("#", "---"))
            )
        )
        if values["backend"]["envVars"].get("ENVIRONMENT") != target:
            raise RuntimeError("Application environment does not match the delivery")
        if target == "production" and "staging" in values.get("ingress", {}).get("host", ""):
            raise RuntimeError("Production cannot reuse the staging hostname")
        template.unlink()
        (chart / "values.yaml").write_text(json.dumps(values, indent=2) + "\n")
        shutil.copyfile(
            Path(__file__).with_name("staging-smoke.yaml"), chart / "templates/staging-smoke.yaml"
        )
        subprocess.run(["helm", "lint", str(chart)], check=True)
        subprocess.run(
            [
                "helm",
                "package",
                str(chart),
                "--destination",
                str(destination),
                "--version",
                version,
                "--app-version",
                sha,
            ],
            check=True,
        )
    sys.stdout.write(f"Prepared {target} chart conversations {version} for {tag} ({sha})\n")


if __name__ == "__main__":
    main()
