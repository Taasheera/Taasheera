"""Publish immutable EC2 images for one commit, and return digest-pinned references.

Run on the GitHub runner after OIDC/ECR login. Existing SHA tags are reused, so a
workflow retry never overwrites a release. Nothing on the live host is touched.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

REGISTRY = "632630731308.dkr.ecr.us-east-1.amazonaws.com"


def build_args(service, revision, environ=os.environ):
    """Sentry release and browser settings baked into the release images (public values only:
    a browser DSN is public by design; the API's DSN stays in the host's private env file)."""
    release = f"taasheera@{revision}"
    if service == "api":
        return ["--build-arg", f"SENTRY_RELEASE={release}"]
    values = {"VITE_SENTRY_RELEASE": release, "VITE_SENTRY_ENVIRONMENT": "production",
              "VITE_SENTRY_DSN": environ.get("SENTRY_FRONTEND_DSN", "").strip(),
              "VITE_SENTRY_TRACES_SAMPLE_RATE": environ.get("SENTRY_FRONTEND_TRACES_SAMPLE_RATE", "").strip() or "0"}
    if values["VITE_SENTRY_DSN"] and not re.fullmatch(r"https://[0-9a-f]+@[A-Za-z0-9.-]+/[0-9]+", values["VITE_SENTRY_DSN"]):
        raise ValueError("SENTRY_FRONTEND_DSN must be a Sentry DSN.")
    if not re.fullmatch(r"0|1|0?\.[0-9]+", values["VITE_SENTRY_TRACES_SAMPLE_RATE"]):
        raise ValueError("SENTRY_FRONTEND_TRACES_SAMPLE_RATE must be between 0 and 1.")
    return [argument for key, value in values.items() for argument in ("--build-arg", f"{key}={value}")]


def publish(revision, *, run=subprocess.run):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A full commit SHA is required.")

    def digest(repository):
        result = run(["aws", "ecr", "batch-get-image", "--region", "us-east-1",
                      "--repository-name", repository, "--image-ids", f"imageTag={revision}",
                      "--output", "json"], check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        if data.get("images"):
            value = data["images"][0]["imageId"]["imageDigest"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
                raise RuntimeError("ECR returned an invalid image digest.")
            return value
        if any(item.get("failureCode") != "ImageNotFound" for item in data.get("failures", [])):
            raise RuntimeError("ECR could not resolve the release image.")
        return None

    images = {}
    for service, dockerfile, context in (("api", "backend/Dockerfile", "backend"),
                                         ("web", "deploy/aws/Dockerfile.web", ".")):
        repository = f"taasheera-ec2-{service}"
        image_digest = digest(repository)
        if image_digest is None:
            tag = f"{REGISTRY}/{repository}:{revision}"
            run(["docker", "build", "--platform", "linux/amd64", "--label",
                 f"org.opencontainers.image.revision={revision}", *build_args(service, revision),
                 "-f", dockerfile, "-t", tag, context], check=True)
            run(["docker", "push", tag], check=True)
            image_digest = digest(repository)
            if image_digest is None:
                raise RuntimeError("Published image is not available from ECR.")
        images[f"TAASHEERA_{service.upper()}_IMAGE"] = f"{REGISTRY}/{repository}@{image_digest}"
    return images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision")
    args = parser.parse_args()
    images = publish(args.revision)
    # Emit the pair only after both images exist. A partial publish cannot start a deployment.
    with Path(os.environ["GITHUB_ENV"]).open("a") as output:
        for key, value in images.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
