import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import install as install_entry
from installers import install_claude, install_codex
from src.hooks import codex_voice_hook, voice_hook


class CodexPayloadTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(voice_hook.DEFAULTS)

    def test_stop_uses_last_assistant_message(self):
        payload = {
            "hook_event_name": "Stop",
            "session_id": "thread-1",
            "cwd": "/tmp/my_project",
            "last_assistant_message": "实现细节很多。\n🔊 已经改好，并且测试通过。",
        }
        with (mock.patch.object(voice_hook, "speak") as speak,
              mock.patch.object(voice_hook, "turn_texts",
                                side_effect=AssertionError("不应读取 transcript"))):
            voice_hook.handle_payload(payload, self.cfg)

        text, cfg, session, label = speak.call_args.args
        self.assertEqual(text, "已经改好，并且测试通过。")
        self.assertEqual(cfg, self.cfg)
        self.assertEqual(session, "thread-1")
        self.assertEqual(label, "my project")

    def test_permission_request_is_spoken(self):
        payload = {
            "hook_event_name": "PermissionRequest",
            "session_id": "thread-2",
            "cwd": "/tmp/demo",
            "tool_name": "Bash",
            "tool_input": {"description": "下载并安装项目依赖"},
        }
        with mock.patch.object(voice_hook, "speak") as speak:
            voice_hook.handle_payload(payload, self.cfg)

        self.assertEqual(speak.call_args.args[0], "需要你批准，下载并安装项目依赖")

    def test_adapter_always_writes_json(self):
        payload = {"hook_event_name": "Stop", "last_assistant_message": "完成"}
        output = io.StringIO()
        with (mock.patch.object(codex_voice_hook.sys, "stdin",
                               io.StringIO(json.dumps(payload))),
              mock.patch.object(voice_hook, "load_config",
                                return_value={"enabled": False}),
              redirect_stdout(output)):
            codex_voice_hook.main()
        self.assertEqual(json.loads(output.getvalue()), {})


class CodexInstallerTests(unittest.TestCase):
    def test_copy_scripts_uses_the_reorganized_source_tree(self):
        with tempfile.TemporaryDirectory() as hook_dir:
            with mock.patch.object(install_codex, "HOOK_DIR", hook_dir):
                install_codex.copy_scripts()
            for name in install_codex.FILES:
                installed = os.path.join(hook_dir, name)
                source = os.path.join(install_codex.HOOK_SOURCE_DIR, name)
                self.assertTrue(os.path.exists(installed))
                with open(installed, "rb") as got, open(source, "rb") as expected:
                    self.assertEqual(got.read(), expected.read())

    def test_install_preserves_other_hooks_and_is_idempotent(self):
        other = {
            "hooks": [{"type": "command", "command": "python3 other.py"}]
        }
        data = {
            "hooks": {
                "Stop": [
                    other,
                    {"hooks": [{"type": "command",
                                "command": install_codex.COMMAND}]},
                ]
            }
        }
        with mock.patch.object(install_codex, "copy_scripts"):
            install_codex.install(data)
            install_codex.install(data)

        stop_groups = data["hooks"]["Stop"]
        commands = [handler["command"]
                    for group in stop_groups
                    for handler in group["hooks"]]
        self.assertEqual(commands.count("python3 other.py"), 1)
        self.assertEqual(commands.count(install_codex.COMMAND), 1)
        for event in install_codex.EVENTS:
            own = [handler for group in data["hooks"][event]
                   for handler in group["hooks"]
                   if handler.get("command") == install_codex.COMMAND]
            self.assertEqual(len(own), 1)


class UnifiedInstallerTests(unittest.TestCase):
    def test_claude_installer_uses_the_reorganized_source_tree(self):
        with tempfile.TemporaryDirectory() as hook_dir:
            installed_hook = os.path.join(hook_dir, "voice_hook.py")
            installed_daemon = os.path.join(hook_dir, "tts_daemon.py")
            with (mock.patch.object(install_claude, "INSTALLED", installed_hook),
                  mock.patch.object(install_claude, "DAEMON_INSTALLED",
                                    installed_daemon),
                  mock.patch.object(install_claude, "build_notch")):
                install_claude.copy_hook()
            for source, installed in (
                (install_claude.SOURCE, installed_hook),
                (install_claude.DAEMON_SOURCE, installed_daemon),
            ):
                with open(installed, "rb") as got, open(source, "rb") as expected:
                    self.assertEqual(got.read(), expected.read())

    def test_default_installs_claude_then_codex(self):
        calls = []
        args = SimpleNamespace(client="all", uninstall=False)
        with (mock.patch.object(install_entry, "parse_args", return_value=args),
              mock.patch.object(install_entry.install_claude, "main",
                                side_effect=lambda value: calls.append(("claude", value))),
              mock.patch.object(install_entry.install_codex, "main",
                                side_effect=lambda value: calls.append(("codex", value))),
              redirect_stdout(io.StringIO())):
            install_entry.main()
        self.assertEqual(calls, [("claude", []), ("codex", [])])

    def test_uninstall_removes_codex_before_shared_backend(self):
        calls = []
        args = SimpleNamespace(client="all", uninstall=True)
        with (mock.patch.object(install_entry, "parse_args", return_value=args),
              mock.patch.object(install_entry.install_claude, "main",
                                side_effect=lambda value: calls.append(("claude", value))),
              mock.patch.object(install_entry.install_codex, "main",
                                side_effect=lambda value: calls.append(("codex", value))),
              redirect_stdout(io.StringIO())):
            install_entry.main()
        self.assertEqual(calls, [("codex", ["--uninstall"]),
                                 ("claude", ["--uninstall"])])


if __name__ == "__main__":
    unittest.main()
