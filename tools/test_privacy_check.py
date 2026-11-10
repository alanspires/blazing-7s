"""tools/privacy_check.py and .githooks/pre-push on temporary repositories, with synthetic
content and a synthetic private literal. Every address, path and literal below is assembled
at run time, so this file passes the checker's own rules."""

from __future__ import annotations

import base64
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import privacy_check as pc

TOOLS = Path(__file__).resolve().parent
CHECKER = TOOLS / "privacy_check.py"
HOOK = TOOLS.parent / ".githooks" / "pre-push"
AT = "@"
NOREPLY = "1+tester" + AT + pc.NOREPLY_DOMAIN.decode()
OTHER_EMAIL = "someone" + AT + "example.org"
HOME_PATH = "/" + "Users" + "/someone/notes.txt"
LITERAL = "Quokka" + " " + "Zebrafish"
ZERO = "0" * 40


def segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload


def jpeg(*extra: bytes, tail: bytes = b"") -> bytes:
    """A structurally complete baseline JPEG with JFIF and ICC segments, `extra` segments after
    them, entropy-coded data holding a stuffed FF and a restart marker, then `tail`."""
    return (b"\xff\xd8" + segment(0xE0, b"JFIF\0\x01\x02\0\0\x01\0\x01\0\0")
            + segment(0xE2, b"ICC_PROFILE\0\x01\x01profile") + b"".join(extra)
            + segment(0xDB, bytes(65)) + segment(0xC0, b"\x08\0\x01\0\x01\x01\x01\x11\0")
            + segment(0xC4, bytes(17) + b"\0") + segment(0xDA, b"\x01\x01\0\0\x3f\0")
            + b"\x12\xff\x00\x34\xff\xd0\x56" + b"\xff\xd9" + tail)


def chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")


def png(*extra: bytes) -> bytes:
    header = (1).to_bytes(4, "big") * 2 + bytes((8, 0, 0, 0, 0))
    return (pc.PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"sRGB", b"\0") + b"".join(extra)
            + chunk(b"IDAT", zlib.compress(b"\0\0")) + chunk(b"IEND", b""))


HEIC = b"\0\0\0\x18ftypheic\0\0\0\0mif1heic"


class RepoTest(unittest.TestCase):
    """A new repository per test, with git and the checker isolated from the host's
    configuration and the identity given explicitly."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        self.env.update(HOME=str(self.base), XDG_CONFIG_HOME=str(self.base / "config"), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL=NOREPLY,
                        GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL=NOREPLY)
        self.git("-c", "init.defaultBranch=main", "init", "-q")
        self.literals(LITERAL)

    def git(self, *args: str, cwd: Path | None = None, **identity: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env | identity, check=True,
                              capture_output=True, text=True).stdout.strip()

    def literals(self, *lines: str) -> None:
        """Write the private-patterns file with these lines; none removes it."""
        path = self.base / "config" / "blazing-7s" / "private-patterns"
        if lines:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n")
        elif path.exists():
            path.unlink()

    def stage(self, files: dict[str, bytes | str]) -> None:
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                content = content.encode()
            path.write_bytes(content)
        self.git("add", "-A", "-f")

    def commit(self, files: dict[str, bytes | str] | None = None, **identity: str) -> str:
        self.stage(files or {})
        self.git("commit", "-q", "--allow-empty", "-m", "change", **identity)
        return self.git("rev-parse", "HEAD")

    def check(self, *argv: str, stdin: str = "") -> tuple[int, set[tuple[str, str]]]:
        """The checker's exit status and its findings as (location, rule)."""
        result = subprocess.run([sys.executable, str(CHECKER), *argv], cwd=self.repo, env=self.env, input=stdin,
                                capture_output=True, text=True, check=False)
        findings = {tuple(line.rpartition(": ")[::2]) for line in result.stdout.splitlines()}
        return result.returncode, findings


class TreeTest(RepoTest):
    def test_clean_images_and_text_pass(self):
        self.stage({"photo.jpg": jpeg(), "chart.png": png(),
                    "notes.md": f"Commits use {NOREPLY}; brew lives in /home/linuxbrew/.linuxbrew\n"})
        self.assertEqual(self.check("tree"), (0, set()))

    def test_image_metadata_trailing_data_and_other_formats_fail(self):
        self.stage({"exif.jpg": jpeg(segment(0xE1, b"Exif\0\0MM\0*")), "trailing.jpg": jpeg(tail=b"more"),
                    "itxt.png": png(chunk(b"iTXt", b"Comment\0\0\0\0\0text")), "exif.png": png(chunk(b"eXIf", b"MM\0*")),
                    "photo.heic": HEIC})
        self.assertEqual(self.check("tree"), (pc.FINDINGS, {
            ("exif.jpg", pc.IMAGE_METADATA), ("trailing.jpg", pc.IMAGE_TRAILING_DATA),
            ("itxt.png", pc.IMAGE_METADATA), ("exif.png", pc.IMAGE_METADATA), ("photo.heic", pc.IMAGE_TYPE)}))

    def test_text_with_a_home_path_or_other_email_fails_but_binaries_are_not_read_as_text(self):
        self.stage({"home.md": f"see {HOME_PATH}\n", "mail.md": f"ask {OTHER_EMAIL}\n",
                    "vendor.bin": b"\0" + OTHER_EMAIL.encode()})
        self.assertEqual(self.check("tree"), (pc.FINDINGS, {("home.md", pc.HOME_PATH), ("mail.md", pc.EMAIL)}))

    def test_a_private_literal_is_found_in_each_representation(self):
        plist = plistlib.dumps({"author": LITERAL}, fmt=plistlib.FMT_BINARY)
        self.stage({"text.md": f"by {LITERAL.upper()}\n", "wide.bin": b"\0" + LITERAL.upper().encode("utf-16-le"),
                    "annotated.pdf": b"%PDF-1.4\n\0/Annot (" + base64.b64encode(plist) + b")\n"})
        self.assertEqual(self.check("tree"), (pc.FINDINGS, {
            ("text.md", pc.PRIVATE_LITERAL), ("wide.bin", pc.PRIVATE_LITERAL_UTF16LE),
            ("annotated.pdf", pc.PRIVATE_LITERAL_BPLIST)}))
        self.assertEqual(self.check("tree", "--no-private-literals"), (0, set()))

    def test_tracked_agent_sessions_and_plans_fail(self):
        self.stage({".plans/release.md": "plan\n", ".pi/session.json": "{}\n"})
        self.assertEqual(self.check("tree"), (pc.FINDINGS, {
            (".plans/release.md", pc.PRIVATE_PATH), (".pi/session.json", pc.PRIVATE_PATH)}))

    def test_a_missing_or_empty_literals_file_fails_unless_explicitly_skipped(self):
        self.stage({"notes.md": "clean\n"})
        self.literals()
        self.assertEqual(self.check("tree"), (pc.SETUP_ERROR, set()))
        self.literals("", "  ")
        self.assertEqual(self.check("tree"), (pc.SETUP_ERROR, set()))
        self.assertEqual(self.check("tree", "--no-private-literals"), (0, set()))

    def test_a_revision_is_checked_instead_of_the_index(self):
        self.commit({"home.md": f"see {HOME_PATH}\n"})
        self.git("rm", "-q", "home.md")
        self.assertEqual(self.check("tree"), (0, set()))
        self.assertEqual(self.check("tree", "HEAD"), (pc.FINDINGS, {("home.md", pc.HOME_PATH)}))


class PushTest(RepoTest):
    def test_outgoing_commit_identities_must_be_noreply_and_free_of_literals(self):
        clean = self.commit({"notes.md": "clean\n"})
        self.assertEqual(self.check("push", "origin", "url", stdin=f"refs/heads/main {clean} refs/heads/main {ZERO}\n"),
                         (0, set()))
        leaky = self.commit(GIT_AUTHOR_EMAIL=OTHER_EMAIL, GIT_COMMITTER_NAME=LITERAL)
        self.assertEqual(self.check("push", "origin", "url", stdin=f"refs/heads/main {leaky} refs/heads/main {clean}\n"),
                         (pc.FINDINGS, {(f"commit {leaky} author", pc.IDENTITY_EMAIL),
                                        (f"commit {leaky} committer", pc.IDENTITY_NAME)}))
        later = self.commit()
        self.assertEqual(self.check("push", "origin", "url", stdin=f"refs/heads/main {later} refs/heads/main {leaky}\n"),
                         (0, set()))

    def test_every_outgoing_commit_has_its_blobs_checked_not_only_the_tip(self):
        added = self.commit({"home.md": f"see {HOME_PATH}\n"})
        self.git("rm", "-q", "home.md")
        tip = self.commit()
        self.assertEqual(self.check("push", stdin=f"refs/heads/main {tip} refs/heads/main {ZERO}\n"),
                         (pc.FINDINGS, {(f"{added}:home.md", pc.HOME_PATH)}))

    def test_a_tag_pointing_at_a_tree_or_a_blob_has_its_blobs_checked(self):
        self.stage({"home.md": f"see {HOME_PATH}\n"})
        tree = self.git("write-tree")
        self.git("tag", "-a", "-m", "snapshot", "snapshot", tree)
        self.stage({"mail.md": f"ask {OTHER_EMAIL}\n"})
        blob = self.git("rev-parse", ":mail.md")
        self.git("tag", "loose", blob)
        snapshot = self.git("rev-parse", "refs/tags/snapshot")
        refs = (f"refs/tags/snapshot {snapshot} refs/tags/snapshot {ZERO}\n"
                f"refs/tags/loose {blob} refs/tags/loose {ZERO}\n")
        self.assertEqual(self.check("push", stdin=refs),
                         (pc.FINDINGS, {(f"{tree}:home.md", pc.HOME_PATH), ("refs/tags/loose", pc.EMAIL)}))


class HookTest(RepoTest):
    def bare_remote_behind_the_hook(self) -> Path:
        """An empty bare remote, with the checker, this file and the hook staged and enabled."""
        remote = self.base / "remote.git"
        self.git("init", "-q", "--bare", str(remote))
        (self.repo / "tools").mkdir()
        (self.repo / ".githooks").mkdir()
        for source, target in ((CHECKER, "tools"), (Path(__file__).resolve(), "tools"), (HOOK, ".githooks")):
            shutil.copy2(source, self.repo / target / source.name)
        self.git("config", "core.hooksPath", ".githooks")
        return remote

    def test_the_hook_passes_the_checker_itself_and_blocks_a_push_adding_an_email(self):
        remote = self.bare_remote_behind_the_hook()
        first = self.commit()
        self.git("push", "-q", str(remote), "HEAD:refs/heads/main")
        self.assertEqual(self.git("rev-parse", "refs/heads/main", cwd=remote), first)

        self.commit({"mail.md": f"ask {OTHER_EMAIL}\n"})
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("push", "-q", str(remote), "HEAD:refs/heads/main")
        self.assertEqual(self.git("rev-parse", "refs/heads/main", cwd=remote), first)

    def test_the_hook_blocks_a_clean_tag_that_points_at_a_tag_with_a_private_tagger(self):
        remote = self.bare_remote_behind_the_hook()
        self.commit()
        self.git("tag", "-a", "-m", "candidate", "candidate", "HEAD", GIT_COMMITTER_EMAIL=OTHER_EMAIL)
        self.git("tag", "-a", "-m", "release", "release", "candidate")
        self.git("tag", "-d", "candidate")  # only the outer tag is pushed; it still carries the inner one
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("push", "-q", str(remote), "refs/tags/release")
        self.assertEqual(self.git("for-each-ref", "refs/tags", cwd=remote), "")


if __name__ == "__main__":
    unittest.main()
