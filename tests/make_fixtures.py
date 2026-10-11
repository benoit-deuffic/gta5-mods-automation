"""Generates tiny local mock fixtures used by MeshyClient in mock mode.

Run once: `python tests/make_fixtures.py`. Produces:
- tests/fixtures/mock_humanoid.fbx : a minimal valid ASCII FBX (empty mesh is
  fine, it's only used to exercise file-presence/size checks and the mock
  Blender path, never fed to a real Blender/Sollumz import in this repo).
- tests/fixtures/mock_texture.png  : a small power-of-two PNG (64x64).
"""
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def make_ascii_fbx(path: Path, padding_bytes: int = 21000) -> None:
    header = (
        "; FBX 7.3.0 project file\n"
        "; Mock fixture for gtav-ai-pipeline tests, not a real mesh.\n"
        "FBXHeaderExtension:  {\n"
        "    FBXHeaderVersion: 1003\n"
        "    FBXVersion: 7300\n"
        "}\n"
    )
    # Pad with a comment block so the file clears the min-size validator threshold.
    padding = "; " + ("0" * (padding_bytes - len(header))) + "\n"
    path.write_text(header + padding, encoding="ascii")


def make_png(path: Path, size: int = 64) -> None:
    import struct
    import zlib

    width = height = size
    raw = b"".join(
        b"\x00" + bytes(
            v
            for x in range(width)
            for v in (x * 255 // width, y * 255 // height, 128)
        )
        for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    make_ascii_fbx(FIXTURES_DIR / "mock_humanoid.fbx")
    make_png(FIXTURES_DIR / "mock_texture.png")
    print(f"Fixtures written to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
