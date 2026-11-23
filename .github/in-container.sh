#!/usr/bin/env bash
# Run a command in the CI-built dev container image with the checkout mounted. The
# runner's uid owns the files, so builds and caches can write and git trusts the tree.
# /tmp is in memory: the web control page's browser tests keep its journal there, and every
# journal append fsyncs, so on a busy disk an odds apply could otherwise outlast their waits.
set -euo pipefail
exec docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --tmpfs /tmp:rw,exec,mode=1777 \
    -v "$PWD:/workspace" -w /workspace blazing-7s-dev "$@"
