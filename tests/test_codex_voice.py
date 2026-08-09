import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import codex_voice_hook
import install_codex
import voice_hook


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


if __name__ == "__main__":
    unittest.main()
