#!/usr/bin/env bash
# Run a command in the CI-built dev container image with the checkout mounted. The
# runner's uid owns the files, so builds and caches can write and git trusts the tree.
set -euo pipefail
exec docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$PWD:/workspace" -w /workspace blazing-7s-dev "$@"
