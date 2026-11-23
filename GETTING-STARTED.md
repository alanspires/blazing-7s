# Getting started

How to run the tests, build the experimental firmware, open the Ghidra project and
connect the SAS tools to your own machine, for anyone with a fresh clone. Everything
the tests and the firmware build read is in the clone: the ROM dumps in `roms/` and
the captured machine replies in `tools/fixtures/`. The vendor documents (`vendor-docs/`) and
the Ghidra project (`ghidra/`) are there for reference. Nothing in the tests or the
build touches a real machine; the tests drive scripted fakes and a CPU emulator.

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
  test_sas_commission test_privacy_check test_sas_machine test_web_journal \
  test_web_state test_web_worker test_web_views test_web_app \
  test_web_control_live_check

# the full suite, including the compiled-firmware CPU tests (several minutes)
docker run --rm -v "$PWD":/work -w /work blazing-7s-dev python -m pytest firmware/tests tools

# only the web control page in Chromium (the image ships the pinned Chromium)
docker run --rm -v "$PWD":/work -w /work blazing-7s-dev python -m pytest tools/test_web_browser.py

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
firmware/.venv/bin/playwright install chromium   # for tools/test_web_browser.py
```

Then, from the repository root:

```sh
# host tool tests: system python3, standard library only, from inside tools/
(cd tools && python3 -m unittest \
  test_sas_protocol test_sas_commands test_sas_cli test_sas_write_test \
  test_sas_money_test test_bsg_frame test_sas_profile test_sas_extension \
  test_sas_commission test_privacy_check test_sas_machine test_web_journal \
  test_web_state test_web_worker test_web_views test_web_app \
  test_web_control_live_check)

# the full suite, including the compiled-firmware CPU tests (several minutes)
firmware/.venv/bin/python -m pytest firmware/tests tools

# only the web control page in Chromium
firmware/.venv/bin/python -m pytest tools/test_web_browser.py

# the firmware build, into firmware/build/
firmware/.venv/bin/python firmware/build.py firmware/patches.json
```

On Linux, use the dev container; it is the tested Linux route.

## What the tests cover

The host-tool tests (`tools/test_*.py` in the unittest list above) need only the
Python standard library. The SAS command table and the J3 CRC are checked against the
ROM; each SAS command runs through the CLI against a scripted machine. Real replies in
`tools/fixtures/` must decode consistently; captured profile-switch replies also
exercise the per-operation receive budget without wall-clock waits. The web control
page's suites (`test_web_*` and `test_sas_machine`) run its machine operations, journal,
worker, views and HTTP server against the scripted machine, including a reply lost at
each money, spin, outcome and profile step, and the live-check script end to end against
the real server on that machine.

The full pytest suite adds the firmware tests in `firmware/tests/`, which run the
compiled extension and the original ROM routines around it in a CPU emulator, and the
tools tests that need the firmware environment: the commissioning wrapper and the
compiled-firmware integration tests (`tools/test_sas_commission_wrapper.py`,
`tools/test_sas_commission_firmware.py`, `tools/test_sas_firmware.py`), and the browser
suite `tools/test_web_browser.py`, which drives the real web control page in Chromium
through pytest-playwright (live updates across two pages, one-tap actions, lost-reply and
offline disablement, the phone layout).

## Check the build

`firmware/build/main.bin` must match the **Published image SHA-256** in
`firmware/README.md`, **Offline verification recorded**:

```sh
shasum -a 256 firmware/build/main.bin   # macOS; sha256sum on Linux
```

`main.bin` and the two chip images reproduce across the macOS and Linux builds of the
pinned toolchain; the ELF, map and manifest do not
(`firmware/toolchain.md`, **Outputs and reproducibility**). The build
is experimental and never marked installation-ready (`firmware/README.md`).

GitHub Actions (`.github/workflows/ci.yml`) repeats these checks on every push: the full
suite, the build hash and the privacy check inside the dev container image, and the
host-tool stdlib suites on Python 3.9.

## Talking to a real machine

The SAS tools run on a Linux computer wired to the machine's J10 port through an
FTDI USB RS-232 adapter. `notes/sas-j10.md` covers the wiring, the voltage check and
**SAS host setup**. In short, from another computer (macOS or Linux) with passwordless
SSH to that host and `rsync` on both ends:

```sh
export SAS_HOST=user@sas-host SAS_PORT=/dev/ttyUSB0   # required; no defaults
tools/sas meters
```

`tools/sas list`, `tools/sas help`, `tools/sas profile-stock` and
`tools/sas profile-preview` work without either variable. On the SAS host itself, run
`python3 tools/sas.py --port <device> <command>`. The firmware extension commands need
the modified firmware (`firmware/README.md`); state-changing commands act at once,
without confirmation. Read `notes/sas-j10.md`, **Money test**, before running
`tools/sas money-test`: it withdraws the entire cashable balance before restoring it,
and every movement stays in the lifetime meters. The payout and outcome features are
for a privately owned machine used for entertainment, not for regulated or commercial
gaming machines.

## The web control page

`tools/web_control.py` serves a page that controls the machine from any phone or laptop
on your network: credits in and whole-balance cash out, live state and meters, remote
spins, picking the next result, the odds and payouts editor, restoring the stock game,
and machine settings. It runs on the SAS host, owns the serial line while it runs, and
keeps an activity log and command journal across restarts. It needs the modified firmware
(`firmware/README.md`): it reads the extension's INFO and STATUS with every state read.
Anyone who can reach the page can use every control, with no login or confirmation, so
serve it only on a network you trust.

On the SAS host, from a checkout (the page reads `tools/` and `roms/`), with pyserial
installed for the interpreter:

```sh
python3 tools/web_control.py --port /dev/serial/by-id/<adapter> \
  --listen <address>:<port> --journal <journal-file>
```

Every argument is required; there are no defaults. `--listen` takes the address to bind
(`[address]:port` for IPv6). The page answers only requests whose host is the `--listen`
address or a name given with `--allow-host` (repeatable), so when people open it by a
host name, or by an address other than the one it binds (such as `0.0.0.0`), list that
name or address with `--allow-host`. `--address` sets the machine's SAS address
(default 1). The journal's directory must exist; the file is created with mode 0600,
because its command records hold the machine's AFT registration key.

The page must have the serial port to itself: stop any other program holding the port
before starting it, and do not run `tools/sas` against the machine while it runs. When a
command's reply is lost, the page never resends it: it reads the machine, says what
happened, and keeps that kind of action blocked until it knows (`.rules/web-control.md`).

### Running the web control page as a service

`tools/web_control.service` is a systemd unit template with placeholders only. To install
it on a Linux SAS host:

1. **Serial device.** Use the adapter's stable path under `/dev/serial/by-id/`, which
   survives reboots and replugging, rather than a `/dev/ttyUSBn` name. Find it with
   `ls -l /dev/serial/by-id/`.
2. **Permissions.** The service user needs the serial group: on most distributions that
   is `dialout` (`sudo usermod -aG dialout <user>`; the unit also adds it through
   `SupplementaryGroups`). Check the device's group with `ls -l` on its by-id path.
3. **Journal directory.** Create the directory that holds `<journal-file>` and give it to
   the service user: `sudo install -d -o <user> -m 0700 <journal-directory>`.
4. **Interpreter.** Pick a Python 3.9 or newer that has pyserial (a distribution's
   `python3-serial` package, or a venv with `pip install pyserial`), and use its absolute
   path for `<python3>`.
5. **Address.** Bind the host's LAN address, for example `--listen <lan-address>:<port>`,
   so phones on the network can reach it; the unit waits for `network-online.target`
   before binding. Add `--allow-host <name>` to `ExecStart` for each host name people
   type to open the page, since the host check accepts only the `--listen` address and
   the `--allow-host` names.
6. **Free the port.** Stop and disable any other program that holds the serial port,
   such as a console logger or another SAS host program.
7. **Install and enable.** Copy the template, replace every `<placeholder>`, and start it:

   ```sh
   sudo cp tools/web_control.service /etc/systemd/system/web_control.service
   sudoedit /etc/systemd/system/web_control.service    # replace every <placeholder>
   sudo systemd-analyze verify /etc/systemd/system/web_control.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now web_control.service
   ```

8. **Verify.** `systemctl status web_control.service` shows it running,
   `journalctl -u web_control.service` shows its errors, and
   `curl http://<listen-address:port>/state.json` from another computer shows the
   machine's link and credits. It starts at boot and restarts when it fails.
9. **Stop.** `sudo systemctl stop web_control.service` frees the serial port (for
   `tools/sas`, for example); `sudo systemctl disable --now web_control.service` also
   keeps it from starting at boot.

`tools/web_control_live_check.py --url <page-url> --credits <n>` is the scripted check
against a running page on the real machine: it reads the state, plays one remote spin and
adds `<n>` credits, and prints one redacted JSON record. It moves money and plays a game,
which stay in the lifetime meters.

## Opening the Ghidra project

The project needs Ghidra 12.2 or newer (Java 25 or newer). Open it with a neutral user name so
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
