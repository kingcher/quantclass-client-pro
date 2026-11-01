import tempfile
import unittest
from pathlib import Path

from client_mini.trading_time import load_trading_days


class TradingCalendarTests(unittest.TestCase):
    def test_supported_encodings(self):
        text = "交易日期,说明\n2026/11/2,测试\n2026-11-03,测试\n2026/11/2,重复\n"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
                with self.subTest(encoding=encoding):
                    (root / "period_offset.csv").write_bytes(text.encode(encoding))
                    self.assertEqual(load_trading_days(root), ["2026-11-02", "2026-11-03"])

    def test_invalid_encoding_does_not_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "period_offset.csv").write_bytes(b"\xff\xff\xff")
            self.assertEqual(load_trading_days(root), [])


if __name__ == "__main__":
    unittest.main()
