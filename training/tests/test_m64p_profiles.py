from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.m64p_profiles import apply_profile_to_file, load_m64p_config, verify_profile_file  # noqa: E402


class M64PProfileTests(unittest.TestCase):
    def test_apply_reverse_human_profile(self) -> None:
        base_cfg_text = """
[CoreEvents]
Kbd Mapping Stop = 27
Kbd Mapping Save State = 286
Kbd Mapping Load State = 288
Kbd Mapping Reset = 290
Kbd Mapping Slot 0 = 48
Kbd Mapping Slot 1 = 49

[Input-SDL-Control1]
A Button = "key(304)"
""".strip() + "\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            base_cfg = tmp_path / "base.cfg"
            out_cfg = tmp_path / "out.cfg"
            base_cfg.write_text(base_cfg_text, encoding="utf-8")

            report = apply_profile_to_file(base_cfg=base_cfg, out_cfg=out_cfg, profile_name="reverse_human")
            self.assertTrue(report["ok"])

            parser = load_m64p_config(out_cfg)
            self.assertEqual(parser.get("CoreEvents", "Kbd Mapping Stop"), "282")
            self.assertEqual(parser.get("CoreEvents", "Kbd Mapping Save State"), "0")
            self.assertEqual(parser.get("CoreEvents", "Kbd Mapping Slot 0"), "0")
            self.assertEqual(parser.get("Input-SDL-Control1", "A Button"), '"key(304)"')

            verify_report = verify_profile_file(out_cfg, profile_name="reverse_human")
            self.assertTrue(verify_report["ok"])


if __name__ == "__main__":
    unittest.main()
