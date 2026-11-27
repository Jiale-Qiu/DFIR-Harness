from pathlib import Path

# (artifact_key, magic_bytes, offset)
SIGNATURES = [
    ("evtx", b"ElfFile\x00", 0),
    ("registry", b"regf", 0),
    ("prefetch", b"MAM\x04", 0),
    ("prefetch", b"SCCA", 4),
    ("mft", b"FILE0", 0),
    ("mft", b"FILE*", 0),
]

_HEADER_READ_SIZE = max(offset + len(magic) for _, magic, offset in SIGNATURES)


def testFileMagic(item: Path | str, magicBytes: bytes, offset: int = 0) -> bool:
    try:
        with open(item, "rb") as f:
            f.seek(offset)
            return f.read(len(magicBytes)) == magicBytes
    except OSError:
        return False


def _match_header(header: bytes) -> str | None:
    for category, magic, offset in SIGNATURES:
        if header[offset : offset + len(magic)] == magic:
            return category
    return None


def scanDirectory(directory: Path | str) -> dict[str, list[Path]]:
    target_path = Path(directory)
    files: dict[str, list[Path]] = {"evtx": [], "registry": [], "prefetch": [], "mft": []}

    if not target_path.is_dir():
        return files

    for item in target_path.rglob("*"):
        try:
            if not item.is_file():
                continue
            # Read the header once per file rather than reopening for each signature
            with item.open("rb") as f:
                header = f.read(_HEADER_READ_SIZE)
        except OSError:
            continue

        category = _match_header(header)
        if category:
            files[category].append(item)

    return files
