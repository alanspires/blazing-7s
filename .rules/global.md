# blazing-7s

Reverse engineering of one Bally ProSlot 6000 "Blazing 7s" machine that the user owns, plus host tools and an experimental firmware extension for it. This repository is public. `README.md` is the project index; when you add, rename or remove a note, tool or user-facing command, update its entry there. `GETTING-STARTED.md` holds setup and the test and build commands.

## Ground truth

- `roms/*.bin` are the original dumps, merged for the 16-bit bus: the even chip (U28 / U20) supplies the high byte at even addresses. Never modify them; tools, tests and the firmware build read them as hash-pinned inputs and as test oracles.
- The machine runs main program S6M00002B5 with personality **E595211X-05**, despite its "SMI7156" sticker; the SAS ROM signature confirmed the pairing (`roms/README.md`). Addresses in notes, code and tests are CPU addresses in that main program's map unless stated otherwise.
- Prefer checking a claim against the ROM bytes (ideally in a test that reads `roms/`) over restating a number. Function names cited in notes and code are the ones set in the Ghidra project (`.rules/ghidra.md`).
- `docs/` holds vendor manuals, vendor software and the IGT SAS 6.02 specification; they and `roms/` are third-party works, not covered by the MIT `LICENSE`. Code cites spec sections or tables for every layout; keep those citations when changing a layout.
- `notes/` are the findings records. Mark each finding **verified** (checked against ROM bytes, schematic, the live machine, or dated photographs of it) or **inferred** (read from code, not observed), and date live-machine records. Photographs that are not in `photos/` are cited as dated, undistributed observations; cite a `photos/` file only where it shows the item.

## Live machine

- Anything that reaches the real machine (online `tools/sas` commands over SSH to the SAS host, serial probing tools, programming EPROMs, swapping chips) needs the user's explicit approval for that operation. Tests never touch hardware: serial lines, SSH and the RTOS are scripted fakes.
- `tools/sas` online commands require `SAS_HOST` and `SAS_PORT` (no defaults; `SAS_DIR` defaults to `blazing-7s`), and `tools/sas.py` and `tools/sas_commission.py` require `--port` for serial commands. Never add a default host, port or path (`.rules/sas-host.md`).
- Meters are permanent: any money movement stays in lifetime accounting even when the balance is restored.
- A missing or uncertain reply to a state-changing command is an uncertain outcome, never permission to resend. Reconcile machine state first; the owning rule gives the procedure (`.rules/sas-host-machine-tests.md`, `.rules/firmware-remote-control.md`).

## Privacy

Privacy wins over detail: when a note, fixture, test or photo would carry a personal trace, the trace goes.

- Never commit:
  - personal names, or email addresses other than the GitHub noreply address;
  - home-directory or synced-folder paths;
  - private hostnames, or host, user or path defaults that point at one;
  - serial, asset or order numbers of the cabinet or its parts, in text, captured fixtures, or legible in photos;
  - location or device metadata in images;
  - anything under `.pi/` or `.plans/` (both are in `.gitignore`).

  Name such details by category only; never write the values, here or anywhere else in the repository.
- Images must be JPEG or PNG and pass the check below. Inspect every new photo at full resolution and pixelate any identifier label before adding it.
- New vendor binaries (PDFs, office files, executables) need a manual metadata review: the check cannot see inside compressed PDF and office streams, and it cannot read text photographed in images.
- Commit with the GitHub noreply identity.
- Before committing, run `python3 tools/privacy_check.py tree`, and enable the pre-push hook once per checkout with `git config core.hooksPath .githooks`. The check reads private literals from `${XDG_CONFIG_HOME:-~/.config}/blazing-7s/private-patterns` outside the repository; without that file it fails unless given `--no-private-literals`. Findings name a path and a rule id, never the matched text.

## Verification

- Host tools: the stdlib suites run with system `python3` from inside `tools/` (modules import each other as top-level names):

  ```sh
  cd tools && python3 -m unittest test_sas_protocol test_sas_commands test_sas_cli \
    test_sas_write_test test_sas_money_test test_bsg_frame test_sas_profile \
    test_sas_extension test_sas_commission test_privacy_check
  ```

- Firmware and every pytest-based tools test run with `firmware/.venv/bin/python -m pytest ...` from the repository root (the full suite: `firmware/.venv/bin/python -m pytest firmware/tests tools`): there are no `__init__.py` files, and `python -m` puts the root on `sys.path` so `firmware.*` and `tools.*` imports resolve. Setup and per-slice commands are in `.rules/firmware.md`; the dev container runs the same commands (`GETTING-STARTED.md`).
- Run `ruff check` on Python you touch; there is no committed ruff configuration, so defaults apply.
