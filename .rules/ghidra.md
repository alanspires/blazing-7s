---
paths:
  - "ghidra/**"
---

# Ghidra project

The tracked Ghidra project `ghidra/proj/bally.gpr` holds the analysis of main program S6M00002B5. Its function and label names are the shared vocabulary of `notes/`, tool docstrings and firmware comments.

## Requirements

- The program database is format version 33, so it needs Ghidra 12.2 or newer; older releases cannot open it. Ghidra 12.2 needs Java 25 or newer (`application.java.min` in the install's `Ghidra/application.properties`).
- Ghidra rejects project paths that have any element starting with `.`. A checkout under such a path (for example a `.worktrees/` checkout) can't open the project in place. `run-headless.sh` copies it to `/tmp` first, so it is unaffected.

## Project state

- The project database under `ghidra/proj/bally.rep/` is committed. Save from the GUI and commit the resulting database and `projectState` together. `.gitignore` excludes Ghidra's lock, temp, backup and change/snapshot files, which must never be committed.
- `projectState` is generated XML with CRLF line endings; `.gitattributes` sets `cr-at-eol` for it so whitespace checks accept it. Do not normalize its line endings.
- Renaming a function or label changes the vocabulary that notes and code cite (for example `bsg_send_frame`, `sas_rx_byte`, `duart1_init_9600_8n1`). Update those citations in the same change, or keep the existing name.

## No personal traces

The project carries no owner and no absolute paths, so it opens for any user on any machine.
- `project.prp` has no `OWNER` entry; with one, Ghidra refuses every other user (`NotOwnerException`, even for read-only headless runs). Remove it if a save adds it back.
- `projectState`'s `ProgramManagerPlugin` `LOCATION_0` is intentionally empty (`VALUE=""`). Ghidra writes the project's absolute directory there when the GUI closes; reset it to `""` before committing. A relative value doesn't work: Ghidra rejects it with "Absolute path required". An empty value only means the GUI doesn't reopen the program automatically.
- The program's executable path is the repository-relative `roms/s6000_main_S6M00002B5.bin`, and Program Information has no `FSRL`. Don't re-import the ROM into this project; an import records the importer's absolute path again.
- Label and comment history rows record the OS user name of whoever made each change, and saved database files keep stale copies of old record bytes. Start Ghidra with a neutral user name so new rows carry no personal name: `GHIDRA_HEADLESS_JAVA_OPTIONS=-Duser.name=anon` for `analyzeHeadless`, `JAVA_TOOL_OPTIONS=-Duser.name=anon` for the GUI. Before committing a save, run `python3 tools/privacy_check.py tree`; it catches a name or home path anywhere in the project files.

## Scripts

- `ghidra/run-headless.sh <Script.java>...` copies the project to a scratch directory, drops locks and runs the scripts with `-noanalysis -readOnly`, so it works while the GUI holds the lock and **discards every change**. Use it for read-only reports; changes that must persist need the GUI (or a headless run against the real project with the GUI closed).
- It finds `analyzeHeadless` under `GHIDRA_INSTALL_DIR`, falling back to `$(brew --prefix ghidra)/libexec`. If neither has one, it fails and names `GHIDRA_INSTALL_DIR`.
- Java lookup: Ghidra's launcher needs a working `java` to start its own JDK finder, which then picks a supported JDK. The script keeps the `java` already on `PATH` if it runs. If it doesn't (macOS ships a stub `/usr/bin/java`, and Homebrew's openjdk is keg-only), the script puts `$JAVA_HOME/bin` first on `PATH`, else `$(brew --prefix openjdk@<application.java.min>)/bin`. If none of these exists, it fails and names `JAVA_HOME`. The script hard-codes no install path and no JDK path or version.
- Its output filter keeps only `println` lines and errors.
- `S6000Setup.java` must stay idempotent (safe to rerun on a set-up program). It derives the repository root from its own source location and loads `roms/personality_E595211X-05.bin` from there, so it only works from `ghidra/scripts/` inside this checkout.
- The memory map in `S6000Setup.java` comes from the MK14-S6MOD-0001 schematics (main ROM, personality, scratch RAM, SafeRAM, three MC68681 DUARTs whose register n sits at `base + 2n + 1`). Firmware and notes rely on the same addresses; change them only with schematic evidence.
