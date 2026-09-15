"""Independent synthetic headers check truncation, checksum and version handling."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location("arktest", Path(__file__).resolve().parents[1] / "app/arktest.py")
ark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ark)


class HeaderTest(unittest.TestCase):
    def abc(self):
        data = bytearray(60)
        data[:8] = b"PANDA\0\0\0"
        data[12:16] = bytes([24, 0, 0, 0])
        struct.pack_into("<I", data, 16, len(data))
        struct.pack_into("<I", data, 8, zlib.adler32(data[12:]))
        return data

    def inspect(self, data):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "input.abc"
            p.write_bytes(data)
            return ark.inspect(p)

    def test_valid_header(self):
        self.assertEqual(self.inspect(self.abc())["version"], "24.0.0.0")

    def test_truncation(self):
        with self.assertRaises(ValueError):
            self.inspect(self.abc()[:20])

    def test_declared_length(self):
        data = self.abc(); data += b"x"
        with self.assertRaises(ValueError):
            self.inspect(data)

    def test_checksum(self):
        data = self.abc(); data[-1] ^= 1
        with self.assertRaises(ValueError):
            self.inspect(data)

    def test_path_escape(self):
        with self.assertRaises(ValueError):
            ark.safe_path(Path('/tmp/corpus'), '../escape.abc')
