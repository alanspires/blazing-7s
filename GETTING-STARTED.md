# Getting started

Everything the tests and the firmware build need is in the clone: the ROM dumps, the
vendor documents and the Ghidra project. Nothing here touches a real machine; the
tests drive scripted fakes and a CPU emulator.

There are two routes: the dev container, which needs only Docker, or a native
install on macOS.

## Route 1: the dev container

`.devcontainer/` builds an Ubuntu 24.04 image with the pinned `m68k-elf-gcc` 16.2.0 and
`m68k-elf-binutils` 2.47 and the hash-locked Python environment, for arm64 and amd64
hosts. It covers the tests and the firmware build; it has no Ghidra and no access to
serial devices or SSH keys.

In VS Code or GitHub Codespaces, choose **Reopen in Container**. From a shell, at the
repository root:

```sh
docker build -f .devcontainer/Dockerfile -t blazing-7s-dev .

# host tool tests (stdlib unittest, from inside tools/)
docker run --rm -v "$PWD":/work -w /work/tools blazing-7s-dev python3 -m unittest \
  test_sas_protocol test_sas_commands test_sas_cli test_sas_write_test \
  test_sas_money_test test_bsg_frame test_sas_profile test_sas_extension \
  test_sas_commission test_privacy_check

# the full suite, including the compiled-firmware CPU tests (several minutes)
docker run --rm -v "$PWD":/work -w /work blazing-7s-dev python -m pytest firmware/tests tools

# the firmware build, into firmware/build/
docker run --rm -v "$PWD":/work -w /work blazing-7s-dev python firmware/build.py firmware/patches.json
```

The image runs as its non-root user `ubuntu` (uid 1000), and the commands write into
the mounted checkout (`firmware/build/`, caches), so the checkout must be writable by
that user.

## Route 2: native on macOS

Install the cross toolchain and [`uv`](https://docs.astral.sh/uv/) with Homebrew, then
create the hash-locked Python environment (details in `firmware/toolchain.md`):

```sh
brew install m68k-elf-gcc m68k-elf-binutils uv
uv venv --python 3.12 firmware/.venv
uv pip install --python firmware/.venv/bin/python --require-hashes -r firmware/requirements.txt
```

Then, from the repository root:

```sh
# host tool tests: system python3, standard library only, from inside tools/
(cd tools && python3 -m unittest \
  test_sas_protocol test_sas_commands test_sas_cli test_sas_write_test \
  test_sas_money_test test_bsg_frame test_sas_profile test_sas_extension \
  test_sas_commission test_privacy_check)

# the full suite, including the compiled-firmware CPU tests (several minutes)
firmware/.venv/bin/python -m pytest firmware/tests tools

# the firmware build, into firmware/build/
firmware/.venv/bin/python firmware/build.py firmware/patches.json
```

On Linux, use the dev container; it is the tested Linux route.

## Check the build

`firmware/build/main.bin` must match the **Published image SHA-256** in
`firmware/README.md`, **Offline verification recorded**:

```sh
shasum -a 256 firmware/build/main.bin   # macOS; sha256sum on Linux
```

`main.bin` and the two chip images reproduce across toolchain builds; the ELF, map and
manifest do not (`firmware/toolchain.md`, **Outputs and reproducibility**). The build
is experimental and never marked installation-ready (`firmware/README.md`).

## Talking to a real machine

The SAS tools run on a Linux computer wired to the machine's J10 port through an
FTDI USB RS-232 adapter. `notes/sas-j10.md` covers the wiring, the voltage check and
**SAS host setup**. In short, from another computer with passwordless SSH to that host:

```sh
export SAS_HOST=user@sas-host SAS_PORT=/dev/ttyUSB0   # required; no defaults
tools/sas meters
```

`tools/sas list`, `tools/sas help`, `tools/sas profile-stock` and
`tools/sas profile-preview` work without either variable. On the SAS host itself, run
`python3 tools/sas.py --port <device> <command>`. The firmware extension commands need
the modified firmware (`firmware/README.md`); state-changing commands act at once,
without confirmation.

## Opening the Ghidra project

The project needs Ghidra 12.2 or newer (Java 25). Open it with a neutral user name so
your own stays out of the saved history:

```sh
JAVA_TOOL_OPTIONS=-Duser.name=anon ghidraRun ghidra/proj/bally.gpr
```

Ghidra refuses a project path with any element starting with `.`, so open the GUI from
a clone outside such a directory. `ghidra/run-headless.sh` works on a copy in `/tmp` and
is unaffected. See `.rules/ghidra.md`.

## Contributing: keep personal details out

`tools/privacy_check.py` checks for image metadata, home paths, email addresses other
than a GitHub noreply one, `.pi/` or `.plans/` paths, and your own private literals:

```sh
git config core.hooksPath .githooks        # runs the check on every push
python3 tools/privacy_check.py tree        # the staged index, before committing
```

Your private literals (your name, hostnames, serial numbers and the like) go one per
line, case-insensitive, in `${XDG_CONFIG_HOME:-~/.config}/blazing-7s/private-patterns`,
outside the repository. Without that file the check fails; add `--no-private-literals`
after the mode (`tree --no-private-literals`) to run only the generic rules. The hook
always uses the file. The check cannot read compressed streams inside PDFs and office
files, or text in photos: inspect new photos for labels and review the metadata of new
vendor binaries by hand. Commit with your GitHub noreply address.
