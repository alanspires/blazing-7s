#!/bin/bash
# Runs Ghidra scripts headlessly against a scratch copy of the project, so it works while
# the GUI holds the project lock; changes are discarded.
# Usage: run-headless.sh <Script.java> [<Script.java> ...]
# Ghidra comes from GHIDRA_INSTALL_DIR, else from Homebrew's ghidra formula.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

ghidra="${GHIDRA_INSTALL_DIR:-}"
if [ -z "$ghidra" ] && command -v brew >/dev/null; then
  ghidra="$(brew --prefix ghidra 2>/dev/null)/libexec" || ghidra=""
fi
if [ -z "$ghidra" ] || [ ! -x "$ghidra/support/analyzeHeadless" ]; then
  echo "run-headless.sh: Ghidra not found; set GHIDRA_INSTALL_DIR to the Ghidra 12.2+ install directory (the one containing support/analyzeHeadless)" >&2
  exit 1
fi

# Ghidra's launcher needs a working `java` on PATH just to run its JDK finder. Homebrew's
# openjdk is keg-only and macOS ships a stub /usr/bin/java, so supply JAVA_HOME's java or
# the Homebrew openjdk matching the minimum this Ghidra declares.
if ! java -version >/dev/null 2>&1; then
  jmin="$(sed -n 's/^application\.java\.min=//p' "$ghidra/Ghidra/application.properties")"
  jdk=""
  if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
    jdk="$JAVA_HOME/bin"
  elif command -v brew >/dev/null; then
    jdk="$(brew --prefix "openjdk@$jmin" 2>/dev/null)/bin" || jdk=""
  fi
  if [ -z "$jdk" ] || [ ! -x "$jdk/java" ]; then
    echo "run-headless.sh: no Java found; set JAVA_HOME to a JDK $jmin or newer" >&2
    exit 1
  fi
  export PATH="$jdk:$PATH"
fi

scratch="$(mktemp -d /tmp/bally-ghidra.XXXXXX)"
trap 'rm -rf "$scratch"' EXIT
cp -R "$here/proj/bally.gpr" "$here/proj/bally.rep" "$scratch/"
rm -f "$scratch"/*.lock*
scripts=()
for s in "$@"; do scripts+=(-postScript "$s"); done
"$ghidra/support/analyzeHeadless" "$scratch" bally \
  -process s6000_main_S6M00002B5.bin -noanalysis -readOnly \
  -scriptPath "$here/scripts" "${scripts[@]}" 2>&1 \
  | sed -n 's/^INFO  \(.*\) (GhidraScript)  $/\1/p; /ERROR/p; /Exception/p'
