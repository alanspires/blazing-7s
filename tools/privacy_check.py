#!/usr/bin/env python3
"""Check that nothing personal is committed to this repository or leaves it in a push.

  privacy_check.py tree [REV]          every blob in the index, or in REV (a commit or tree)
  privacy_check.py push [REMOTE URL]   the pre-push hook (.githooks/pre-push): reads git's ref
                                       lines on stdin and checks every blob of every outgoing
                                       commit (or tree or blob a pushed ref or tag points at),
                                       and each commit's and tag's identity and message
  --no-private-literals                after the mode: check without the private literals

Private literals are the author's own: one case-insensitive literal per line in
${XDG_CONFIG_HOME:-~/.config}/blazing-7s/private-patterns, kept outside the repository. They
match every blob as raw bytes and as UTF-16LE, and inside base64 binary plists. A missing or
empty file is an error unless --no-private-literals says to go without (visitors, the container).

Each finding is one line, LOCATION: RULE. It names where, never what matched, so the report
itself leaks nothing. Exit status: 0 clean, 1 findings, 2 usage or setup error.

Rules:
  private-path            a tracked path under .pi/ or .plans/
  image-type              an image that is not JPEG or PNG, by content or by file extension
  image-metadata          a JPEG segment or PNG chunk that is not pixel or colour data
  image-malformed         a truncated image, a bad segment or chunk length, or no end marker
  image-trailing-data     bytes after the JPEG EOI or the PNG IEND
  home-path               text naming a home directory (Users or home, not linuxbrew) or Dropbox
  email                   text with an email address other than a GitHub noreply one
  private-literal         a private literal as raw bytes (-utf16le: as UTF-16LE;
                          -bplist: inside a base64 binary plist; -path: in the path)
  identity-email          an author, committer or tagger email that is not a GitHub noreply one
  identity-name           an author, committer or tagger name containing a private literal

Text rules apply to blobs without a NUL byte, and to commit and tag messages. The check cannot
read compressed streams inside PDFs and office files, or text photographed in images.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import NamedTuple

PRIVATE_PATH = "private-path"
IMAGE_TYPE = "image-type"
IMAGE_METADATA = "image-metadata"
IMAGE_MALFORMED = "image-malformed"
IMAGE_TRAILING_DATA = "image-trailing-data"
HOME_PATH = "home-path"
EMAIL = "email"
PRIVATE_LITERAL = "private-literal"
PRIVATE_LITERAL_UTF16LE = "private-literal-utf16le"
PRIVATE_LITERAL_BPLIST = "private-literal-bplist"
PRIVATE_LITERAL_PATH = "private-literal-path"
IDENTITY_EMAIL = "identity-email"
IDENTITY_NAME = "identity-name"

FINDINGS, SETUP_ERROR = 1, 2
PRIVATE_DIRS = frozenset({".pi", ".plans"})
NOREPLY_DOMAIN = b"users.noreply.github.com"
GITLINK = "160000"  # a submodule commit, not a blob

# The patterns are written so that this file does not match them: each literal part is split
# by a character class or a concatenation.
NAME = rb"[A-Za-z0-9._-]+"
HOME_PATH_RE = re.compile(rb"(?<![\w.-])(?:/Users/" + NAME + rb"|/home/(?!linuxbrew(?![A-Za-z0-9._-]))" + NAME
                          + rb"|~/[Dd]ropbox)")
EMAIL_LOCAL_RE = re.compile(rb"[A-Za-z0-9._%+-]{1,64}\Z")  # RFC 5321 bounds the local part
EMAIL_DOMAIN_RE = re.compile(rb"((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
BPLIST_RE = re.compile(re.escape(base64.b64encode(b"bplist")) + rb"[A-Za-z0-9+/]*={0,2}")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CHUNKS = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP",
                        b"pHYs", b"bKGD", b"sBIT"})
JPEG_EOI, JPEG_SOS = 0xD9, 0xDA
JPEG_RST = range(0xD0, 0xD8)
# C0-CF but the reserved JPG (C8) are the frame headers and the DHT and DAC tables; then the
# DQT, DNL and DRI tables and the scan header: everything a decoder needs, nothing else.
JPEG_FRAME_AND_SCAN = frozenset(set(range(0xC0, 0xD0)) - {0xC8}) | {0xDB, 0xDC, 0xDD, JPEG_SOS}
# Application segments allowed by their identifier: JFIF (and its thumbnail extension), the ICC
# colour profile and Adobe's colour transform. Exif, XMP, MPF and comments are not.
JPEG_APP = {0xE0: (b"JFIF\0", b"JFXX\0"), 0xE2: (b"ICC_PROFILE\0",), 0xEE: (b"Adobe",)}
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".tif", ".tiff", ".webp",
                              ".gif", ".bmp"})


def private_patterns_path() -> Path:
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "blazing-7s" / "private-patterns"


class Literals:
    """The private literals in each representation a blob can carry them, lowercased: a blob
    is lowercased once (ASCII only, which also folds UTF-16LE ASCII) and searched for each."""

    def __init__(self, literals: Iterable[str]):
        lowered = [literal.lower() for literal in literals]
        self.raw = [literal.encode() for literal in lowered]
        self.utf16le = [literal.encode("utf-16-le") for literal in lowered]
        # Binary plists store non-ASCII strings as UTF-16BE.
        self.plist = self.raw + self.utf16le + [literal.encode("utf-16-be") for literal in lowered]
        self.names = lowered

    def rules(self, data: bytes) -> set[str]:
        lowered = data.lower()
        found = set()
        if any(form in lowered for form in self.raw):
            found.add(PRIVATE_LITERAL)
        if any(form in lowered for form in self.utf16le):
            found.add(PRIVATE_LITERAL_UTF16LE)
        for match in BPLIST_RE.finditer(data):
            decoded = decode_base64(match.group()).lower()
            if any(form in decoded for form in self.plist):
                found.add(PRIVATE_LITERAL_BPLIST)
                break
        return found

    def in_text(self, text: str) -> bool:
        return any(literal in text.lower() for literal in self.names)


def decode_base64(text: bytes) -> bytes:
    body = text.rstrip(b"=")
    body = body[:len(body) - (len(body) % 4 == 1)]  # one leftover character encodes no byte
    return base64.b64decode(body + b"=" * (-len(body) % 4))


def text_rules(data: bytes) -> set[str]:
    found = set()
    if HOME_PATH_RE.search(data):
        found.add(HOME_PATH)
    at = data.find(b"@")
    while at >= 0:
        domain = EMAIL_DOMAIN_RE.match(data, at + 1)
        if (domain and domain.group(1).lower() != NOREPLY_DOMAIN
                and EMAIL_LOCAL_RE.search(data, max(0, at - 64), at)):
            found.add(EMAIL)
            break
        at = data.find(b"@", at + 1)
    return found


def image_kind(data: bytes) -> str | None:
    """The image format the content starts with, if any."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(PNG_SIGNATURE):
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] in (b"II*\0", b"MM\0*"):
        return "tiff"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp":  # ISO media: HEIC, HEIF, AVIF, and video
        return "iso-media"
    if data[:2] == b"BM" and int.from_bytes(data[2:6], "little") == len(data):
        return "bmp"
    return None


def jpeg_rules(data: bytes) -> set[str]:
    found = set()
    i = 2  # after SOI
    while True:
        if i >= len(data) or data[i] != 0xFF:
            return found | {IMAGE_MALFORMED}
        while i < len(data) and data[i] == 0xFF:  # fill bytes
            i += 1
        if i >= len(data):
            return found | {IMAGE_MALFORMED}
        marker = data[i]
        i += 1
        if marker == JPEG_EOI:
            return found | ({IMAGE_TRAILING_DATA} if i < len(data) else set())
        if i + 2 > len(data):
            return found | {IMAGE_MALFORMED}
        length = int.from_bytes(data[i:i + 2], "big")
        if length < 2 or i + length > len(data):
            return found | {IMAGE_MALFORMED}
        if marker in JPEG_APP:
            if not data.startswith(JPEG_APP[marker], i + 2, i + length):
                found.add(IMAGE_METADATA)
        elif marker not in JPEG_FRAME_AND_SCAN:
            found.add(IMAGE_METADATA)
        i += length
        if marker == JPEG_SOS:
            i = entropy_coded_end(data, i)
            if i < 0:
                return found | {IMAGE_MALFORMED}


def entropy_coded_end(data: bytes, i: int) -> int:
    """Where the marker after a scan's entropy-coded data starts, or -1 if none does."""
    while True:
        i = data.find(b"\xff", i)
        if i < 0 or i + 1 >= len(data):
            return -1
        following = data[i + 1]
        if following != 0x00 and following not in JPEG_RST:  # neither a stuffed FF nor a restart
            return i
        i += 2


def png_rules(data: bytes) -> set[str]:
    found = set()
    i = len(PNG_SIGNATURE)
    while True:
        if i + 8 > len(data):
            return found | {IMAGE_MALFORMED}
        length = int.from_bytes(data[i:i + 4], "big")
        chunk = data[i + 4:i + 8]
        i += 12 + length  # length, type, data, CRC
        if length >= 2 ** 31 or i > len(data):
            return found | {IMAGE_MALFORMED}
        if chunk not in PNG_CHUNKS:
            found.add(IMAGE_METADATA)
        if chunk == b"IEND":
            return found | ({IMAGE_TRAILING_DATA} if i < len(data) else set())


def content_rules(data: bytes, literals: Literals | None) -> tuple[set[str], str | None]:
    """The rules a blob's content breaks, and its image format."""
    kind = image_kind(data)
    if kind == "jpeg":
        found = jpeg_rules(data)
    elif kind == "png":
        found = png_rules(data)
    else:
        found = {IMAGE_TYPE} if kind else set()
    if b"\0" not in data:
        found |= text_rules(data)
    if literals:
        found |= literals.rules(data)
    return found, kind


def path_rules(path: str, kind: str | None, literals: Literals | None) -> set[str]:
    found = {PRIVATE_LITERAL_PATH} if literals and literals.in_text(path) else set()
    parts = PurePosixPath(path).parts
    if PRIVATE_DIRS.intersection(parts[:-1]):
        found.add(PRIVATE_PATH)
    if PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS and kind not in ("jpeg", "png"):
        found.add(IMAGE_TYPE)
    return found


class Entry(NamedTuple):
    path: str
    oid: str


class Objects:
    """Reads objects through one `git cat-file --batch`."""

    def __init__(self, root: Path):
        self.process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE)

    def read(self, oid: str) -> tuple[str, bytes]:
        self.process.stdin.write(oid.encode() + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3:
            raise RuntimeError(f"git cat-file: no object {oid}")
        data = self.process.stdout.read(int(header[2]))
        self.process.stdout.read(1)  # the newline after each object
        return header[1].decode(), data

    def close(self) -> None:
        self.process.stdin.close()
        self.process.stdout.close()
        if self.process.wait():
            raise subprocess.CalledProcessError(self.process.returncode, self.process.args)


class Scanner:
    """Checks blobs and identities, reading each blob once however many paths and commits share it."""

    def __init__(self, root: Path, literals: Literals | None):
        self.root = root
        self.literals = literals
        self.objects = Objects(root)
        self.content: dict[str, tuple[set[str], str | None]] = {}
        self.findings = 0

    def report(self, location: str, rules: Iterable[str]) -> None:
        for rule in sorted(rules):
            print(f"{location}: {rule}", flush=True)
            self.findings += 1

    def blobs(self, entries: Iterable[Entry], prefix: str = "") -> None:
        for entry in entries:
            if entry.oid not in self.content:
                kind, data = self.objects.read(entry.oid)
                if kind != "blob":
                    raise RuntimeError(f"{entry.path}: {entry.oid} is a {kind}, not a blob")
                self.content[entry.oid] = content_rules(data, self.literals)
            found, image = self.content[entry.oid]
            self.report(prefix + entry.path, found | path_rules(entry.path, image, self.literals))

    def message(self, location: str, text: bytes) -> None:
        found = text_rules(text)
        if self.literals:
            found |= self.literals.rules(text)
        self.report(location, found)

    def identity(self, location: str, name: str, email: str) -> None:
        found = set()
        if not email.lower().endswith("@" + NOREPLY_DOMAIN.decode()):
            found.add(IDENTITY_EMAIL)
        if self.literals and self.literals.in_text(name):
            found.add(IDENTITY_NAME)
        self.report(location, found)

    def commit_or_tag(self, oid: str) -> None:
        """Check a commit's or annotated tag's identities and message."""
        kind, data = self.objects.read(oid)
        headers, _, message = data.partition(b"\n\n")
        for line in headers.split(b"\n"):
            role, _, value = line.decode(errors="surrogateescape").partition(" ")
            if role in ("author", "committer", "tagger"):
                person = re.fullmatch(r"(.*?) ?<(.*)> \d+ [+-]\d{4}", value)
                if not person:
                    raise RuntimeError(f"{kind} {oid}: unreadable {role} line")
                self.identity(f"{kind} {oid} {role}", *person.groups())
        self.message(f"{kind} {oid} message", message)

    def close(self) -> None:
        self.objects.close()


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, check=True).stdout


def index_entries(root: Path) -> Iterator[Entry]:
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if record:
            info, _, path = record.partition(b"\t")
            mode, oid, _ = info.decode().split()
            if mode != GITLINK:
                yield Entry(path.decode(errors="surrogateescape"), oid)


def tree_entries(root: Path, rev: str) -> Iterator[Entry]:
    for record in git(root, "ls-tree", "-r", "-z", "--full-tree", rev).split(b"\0"):
        if record:
            info, _, path = record.partition(b"\t")
            _, kind, oid = info.decode().split()
            if kind == "blob":
                yield Entry(path.decode(errors="surrogateescape"), oid)


class Outgoing(NamedTuple):
    commits: list[str]  # oldest first
    tags: list[str]
    trees: list[str]
    blobs: list[Entry]  # each named after the remote ref it is pushed to


def outgoing_objects(root: Path, ref_lines: Iterable[str]) -> Outgoing:
    """What a push sends. A pushed ref points at a commit, tree or blob, or at an annotated tag
    whose target may itself be a tag; every tag in that chain is sent. A new branch sends every
    commit it reaches; an update sends the commits the remote's old value lacks. A tree or blob
    is sent whole."""
    commits: dict[str, None] = {}
    tags: dict[str, None] = {}
    trees: dict[str, None] = {}
    blobs: dict[Entry, None] = {}
    for line in ref_lines:
        fields = line.split()
        if not fields:
            continue
        _, local, remote_ref, remote = fields
        if not local.strip("0"):
            continue  # a deletion sends nothing
        target = local
        while (kind := git(root, "cat-file", "-t", target).strip()) == b"tag":
            tags[target] = None
            field, _, peeled = git(root, "cat-file", "tag", target).partition(b"\n")[0].decode().partition(" ")
            if field != "object":
                raise RuntimeError(f"tag {target}: no object line")
            target = peeled
        if kind == b"tree":
            trees[target] = None
        elif kind == b"blob":
            blobs[Entry(remote_ref, target)] = None
        else:
            revs = [local] if not remote.strip("0") else [local, "--not", remote]
            for commit in git(root, "rev-list", "--reverse", *revs, "--").decode().split():
                commits[commit] = None
    return Outgoing(list(commits), list(tags), list(trees), list(blobs))


def check_tree(scanner: Scanner, rev: str | None) -> None:
    scanner.blobs(tree_entries(scanner.root, rev) if rev else index_entries(scanner.root))


def check_push(scanner: Scanner, ref_lines: Iterable[str]) -> None:
    pushed = outgoing_objects(scanner.root, ref_lines)
    for tag in pushed.tags:
        scanner.commit_or_tag(tag)
    seen: set[Entry] = set()

    def unseen_blobs(rev: str) -> None:
        new = [entry for entry in tree_entries(scanner.root, rev) if entry not in seen]
        seen.update(new)
        scanner.blobs(new, prefix=f"{rev}:")

    for commit in pushed.commits:
        scanner.commit_or_tag(commit)
        unseen_blobs(commit)
    for tree in pushed.trees:
        unseen_blobs(tree)
    scanner.blobs(pushed.blobs)


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-private-literals", action="store_true",
                        help="check without the author's private literals")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)
    tree = modes.add_parser("tree", parents=[common], help="every blob in the index, or in REV")
    tree.add_argument("rev", nargs="?", help="a commit or tree instead of the index")
    push = modes.add_parser("push", parents=[common], help="the pre-push hook; ref lines on stdin")
    push.add_argument("remote", nargs="?", help="the remote's name, as git passes it to the hook")
    push.add_argument("url", nargs="?", help="the remote's URL, as git passes it to the hook")
    args = parser.parse_args(argv)

    literals = None
    if not args.no_private_literals:
        path = private_patterns_path()
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        values = [line.strip() for line in lines if line.strip()]
        if not values:
            print(f"privacy_check: no private literals in {path}; add one per line, or pass "
                  "--no-private-literals to check without them", file=sys.stderr)
            return SETUP_ERROR
        literals = Literals(values)

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    scanner = Scanner(root, literals)
    try:
        if args.mode == "tree":
            check_tree(scanner, args.rev)
        else:
            check_push(scanner, sys.stdin)
    finally:
        scanner.close()
    if scanner.findings:
        print(f"privacy_check: {scanner.findings} findings", file=sys.stderr)
        return FINDINGS
    return 0


if __name__ == "__main__":
    sys.exit(main())
