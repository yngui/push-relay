import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LISTENER = Path(__file__).resolve().parents[1] / "clients" / "windows-toast" / "windows_toast.py"
VARIANT_AUMID = "Microsoft.VisualStudioCode.Example-Build_2.Portable"


class WindowsToastMatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        state = cls.enterClassContext(tempfile.TemporaryDirectory())
        spec = importlib.util.spec_from_file_location("windows_toast", LISTENER)
        assert spec is not None and spec.loader is not None
        cls.listener = importlib.util.module_from_spec(spec)
        with patch("push_relay.state_dir", return_value=Path(state)):
            spec.loader.exec_module(cls.listener)
        cls.addClassCleanup(cls.listener._handler.close)
        cls.addClassCleanup(cls.listener.log.removeHandler, cls.listener._handler)

    def test_chat_toast_app_match(self) -> None:
        cases = (
            ("", VARIANT_AUMID.lower(), True),
            ("", "Microsoft.VisualStudioCode", True),
            ("Visual Studio Code - Insiders", "Other.Id", True),
            ("", "", True),
            ("", "Microsoft.VisualStudioCodeLookalike", False),
            ("", "Microsoft.VisualStudioCode.", False),
            ("", "Microsoft.VisualStudioCode..ExampleBuild", False),
            ("", "Contoso.Microsoft.VisualStudioCode.ExampleBuild", False),
            ("", VARIANT_AUMID + "\n", False),
        )
        for app, aumid, expected in cases:
            with self.subTest(app=app, aumid=aumid):
                self.assertEqual(
                    self.listener.is_target_app(app, aumid, "Session: Test"), expected
                )

    def test_variant_icon_without_app_name(self) -> None:
        self.assertEqual(
            self.listener.resolve_icon(VARIANT_AUMID, ""), "icons/apps/vscode.png"
        )
        self.assertIsNone(self.listener.resolve_icon("Microsoft.VisualStudioCodeLookalike", ""))
        with patch.dict(self.listener.APP_ICON_AUMID, {VARIANT_AUMID: "icons/override.png"}):
            self.assertEqual(
                self.listener.resolve_icon(VARIANT_AUMID, ""), "icons/override.png"
            )

    def test_seen_log_records_category_instead_of_raw_id(self) -> None:
        for aumid, kind in ((VARIANT_AUMID, "vscode"), ("", "missing"), ("Contoso.Other", "other")):
            with self.subTest(kind=kind):
                with self.assertLogs(self.listener.log) as captured:
                    self.listener._log_seen(42, "", aumid, "Session: Test")
                self.assertEqual(
                    [record.getMessage() for record in captured.records],
                    [f"SEEN id=42 app='' aumid_kind={kind} title='Session: Test'"],
                )
