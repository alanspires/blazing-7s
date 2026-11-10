# Build and test environment: the m68k-elf toolchain the firmware build is verified with,
# plus Python 3.12 and the hash-locked firmware/requirements.txt. No hardware access.

# Stage 1 pours exact Homebrew bottles. `brew install <name>` would take whatever version
# is current, so each bottle is fetched by its sha256 and poured from the local file; brew
# still performs the ELF interpreter/RPATH relocation that unpacking by hand would miss.
FROM ghcr.io/homebrew/ubuntu24.04@sha256:63be5353975c43bce2c885742370ab320b3c57541de72fd1458a26a8e35d7d8e AS toolchain
ARG TARGETARCH
ENV HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
RUN <<'SCRIPT'
#!/bin/bash
set -euo pipefail
case "$TARGETARCH" in
    arm64) tag=arm64_linux ;;
    amd64) tag=x86_64_linux ;;
    *) echo "unsupported TARGETARCH '$TARGETARCH' (arm64 or amd64)" >&2; exit 1 ;;
esac
# m68k-elf-gcc 16.2.0 and the runtime dependencies recorded in its bottle tab, with the
# blob digests from that bottle's manifest SBOM.
pins=$(cat <<'PINS'
arm64_linux gmp 6.3.0 5f23ebfcde217dbc697dc961d103ca602445a5775c2ed19a29eaec98f4cfa3c0
arm64_linux isl 0.28 9bb3cf80a3389d78be3ddeb7b6dfddf38ae5e7f036dcb532d53e36bc79bdef18
arm64_linux libmpc 1.4.1 c594f7be54b7cb22a68484e6128f805b49987cec1131873735bdd2b7e01733a9
arm64_linux lz4 1.10.0 e1d4b1bd9069ee0a313d2813d59b3e9217a893aba519707da2ba0b848bc1f727
arm64_linux m68k-elf-binutils 2.47 f26b8058f421a7cc6b03d4a201bd4778924739b5283f66d4c012edf6a8a8c0bf
arm64_linux m68k-elf-gcc 16.2.0 79fd3246d37dac25bf0d3549b4d1024c82b054597f6062e3ad7cceab0c83372b
arm64_linux mpfr 4.2.2 fc5527d722406e18631498339752226ebebecc915bf5da08bfa48cffebeb1ba1
arm64_linux xz 5.8.3 18d983dde14681d8403f46f7fc1a5b743b98b0fa84e3cf4b07153709847a71f9
arm64_linux zlib-ng-compat 2.3.3_1 eb6df814b35273cecd7caf4291ee1a972ab1ac20d11a126b0e2db556dfc50d84
arm64_linux zstd 1.5.7_1 575617c1532fe90e212c052fb14fcd4fa295890e3bc9ac69dc52404a04a95855
x86_64_linux gmp 6.3.0 3dca3544faca889c7389a5fdbd2b5b00582c34a4e14607033573ad3b06ca7882
x86_64_linux isl 0.28 71ff58966339279d9a1012079f817a53d38f269fd6e4c86bc2ea17534a73c86e
x86_64_linux libmpc 1.4.1 734c85661e6c616157f424d9e7199eb83f69a647cb20db6535cc3525b8c6cd9a
x86_64_linux lz4 1.10.0 a8082c2e40dc6d63850f43ea8fa095e55adf18fb0f25ec66bcaee2c4b4438205
x86_64_linux m68k-elf-binutils 2.47 e1df8e469d22a893ddb98a989e732e1128d8bb5f9bbf967a3af885310e38b5fd
x86_64_linux m68k-elf-gcc 16.2.0 2664d84359ca4f0303dd6deeeb48957461c70201dd0d7f39f44a8d056d136bb6
x86_64_linux mpfr 4.2.2 91dfeb407daeeabb3c3caceb89af41dc02b1aa473b88e76c7533efbc2ad6066d
x86_64_linux xz 5.8.3 61032fd340234974371a87c9908c998d9a52bebdf056b83e629ebf7aa038840f
x86_64_linux zlib-ng-compat 2.3.3_1 eb72c27ca6918fb29af89b1287471e381e15156212ccd0e99661ee393bde872d
x86_64_linux zstd 1.5.7_1 3d5ca8948526a2e3a487db0cd0acec9f4b415d5a4b08cffe27e2ea0c339c9dbe
PINS
)
mkdir /tmp/bottles
cd /tmp/bottles
files=()
while read -r bottle_tag formula version sha; do
    [ "$bottle_tag" = "$tag" ] || continue
    file="$formula--$version.$tag.bottle.tar.gz"
    # ghcr serves public Homebrew blobs to this anonymous token.
    curl -fsSL -H 'Authorization: Bearer QQ==' -o "$file" \
        "https://ghcr.io/v2/homebrew/core/$formula/blobs/sha256:$sha"
    echo "$sha  $file" | sha256sum --check --strict -
    files+=("./$file")
done <<< "$pins"
[ "${#files[@]}" -eq 10 ] || { echo "expected 10 bottles for $tag, got ${#files[@]}" >&2; exit 1; }
# --ignore-dependencies keeps brew from replacing pinned dependencies with current ones.
brew install --ignore-dependencies "${files[@]}"
while read -r bottle_tag formula version sha; do
    [ "$bottle_tag" = "$tag" ] || continue
    installed=$(brew list --versions "$formula")
    [ "$installed" = "$formula $version" ] \
        || { echo "brew installed '$installed', expected '$formula $version'" >&2; exit 1; }
done <<< "$pins"
gcc_version=$(m68k-elf-gcc -dumpfullversion)
[ "$gcc_version" = 16.2.0 ] || { echo "m68k-elf-gcc is $gcc_version, expected 16.2.0" >&2; exit 1; }
ld_version=$(m68k-elf-ld --version | sed -n 1p)
[[ "$ld_version" =~ ^GNU\ ld\ \(GNU\ Binutils\)\ 2\.47(\.[0-9]+)?$ ]] \
    || { echo "unexpected m68k-elf-ld: $ld_version" >&2; exit 1; }
multilib=$(m68k-elf-gcc -m68000 -print-multi-directory)
[ "$multilib" = m68000 ] || { echo "-m68000 selects multilib '$multilib', expected m68000" >&2; exit 1; }
# Homebrew itself stays in this stage; drop its link so the copied bin/ has no dangling entry.
rm "$(brew --prefix)/bin/brew"
SCRIPT

# Stage 2 ships only the poured prefix. Its binaries are relocated to this exact path.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash ca-certificates git python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
COPY --from=toolchain /home/linuxbrew/.linuxbrew/Cellar /home/linuxbrew/.linuxbrew/Cellar
COPY --from=toolchain /home/linuxbrew/.linuxbrew/opt /home/linuxbrew/.linuxbrew/opt
COPY --from=toolchain /home/linuxbrew/.linuxbrew/bin /home/linuxbrew/.linuxbrew/bin
COPY --from=toolchain /home/linuxbrew/.linuxbrew/lib /home/linuxbrew/.linuxbrew/lib
# The venv lives outside the workspace so the bind-mounted checkout cannot hide it.
RUN --mount=type=bind,source=firmware/requirements.txt,target=/tmp/requirements.txt \
    python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --only-binary :all: --require-hashes -r /tmp/requirements.txt
ENV PATH=/opt/venv/bin:/home/linuxbrew/.linuxbrew/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
USER ubuntu
