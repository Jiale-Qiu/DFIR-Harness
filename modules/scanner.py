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

def testFileMagic(item, magicBytes, offset=0):
    try:
        with open(item, "rb") as f:
            f.seek(offset)
            file_bytes = f.read(len(magicBytes))
            return file_bytes == magicBytes
    except OSError:
        return False

def scanDirectory(directory):
    target_path = Path(directory)

    files = {
        "evtx": [],
        "registry": [],
        "prefetch": [],
        "mft": [],
    }

    # Search through the main directory
    for item in target_path.rglob("*"):
        if not item.is_file():
            continue

        for category, magic, offset in SIGNATURES:
            if testFileMagic(item, magic, offset):
                files[category].append(item)
                break  # Stop checking once a match is found
            
    return files