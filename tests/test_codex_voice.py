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
from src.daemon import tts_daemon
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


class MuteTests(unittest.TestCase):
    """静音 = 不出声，但 hook 该跑的逻辑照跑。"""

    def test_speak_is_dropped_while_muted(self):
        cfg = dict(voice_hook.DEFAULTS, muted=True)
        with (mock.patch.object(voice_hook, "daemon_request") as request,
              mock.patch.object(voice_hook.subprocess, "Popen") as popen):
            voice_hook.speak("这句不该被念出来", cfg, "s1", "demo")
        request.assert_not_called()     # 没排进队列
        popen.assert_not_called()       # 也没降级到 say

    def test_unmuted_speech_still_reaches_the_daemon(self):
        cfg = dict(voice_hook.DEFAULTS, muted=False)
        with mock.patch.object(voice_hook, "daemon_request",
                               return_value={"ok": True, "queue": 1}) as request:
            voice_hook.speak("这句要念", cfg, "s1", "demo")
        self.assertEqual(request.call_args.args[0]["cmd"], "speak")

    def test_set_muted_persists_and_silences_now(self):
        with tempfile.TemporaryDirectory() as home:
            config = os.path.join(home, "voice_config.json")
            cfg = dict(voice_hook.DEFAULTS)
            with (mock.patch.object(voice_hook, "CONFIG_PATH", config),
                  mock.patch.object(voice_hook, "stop_speaking") as stop):
                voice_hook.set_muted(cfg, True)
                stop.assert_called_once()       # 当前这段立刻掐掉，不等它念完
                with open(config) as f:
                    self.assertTrue(json.load(f)["muted"])

                stop.reset_mock()
                voice_hook.set_muted(cfg, False)
                stop.assert_not_called()
                with open(config) as f:
                    self.assertFalse(json.load(f)["muted"])


class DaemonMuteTests(unittest.TestCase):
    """守护进程要认菜单栏刚写下的静音，不能只认启动时读的那份配置。"""

    def make_engine(self, config, muted):
        with open(config, "w") as f:
            json.dump({"muted": muted}, f)
        with mock.patch.object(tts_daemon, "CONFIG_PATH", config):
            return tts_daemon.Engine(tts_daemon.load_config())

    def test_mute_written_after_startup_is_picked_up(self):
        with tempfile.TemporaryDirectory() as home:
            config = os.path.join(home, "voice_config.json")
            engine = self.make_engine(config, muted=False)
            with mock.patch.object(tts_daemon, "CONFIG_PATH", config):
                self.assertFalse(engine.muted())
                with open(config, "w") as f:
                    json.dump({"muted": True}, f)
                stamp = os.path.getmtime(config) + 10    # mtime 必须真的变
                os.utime(config, (stamp, stamp))
                self.assertTrue(engine.muted())
                self.assertEqual(engine.enqueue("别念", "s1", "demo"), 0)
                self.assertTrue(engine.utt_q.empty())

    def test_hot_reload_leaves_load_time_settings_alone(self):
        with tempfile.TemporaryDirectory() as home:
            config = os.path.join(home, "voice_config.json")
            engine = self.make_engine(config, muted=False)
            loaded_voice = engine.cfg["moss_ref_audio"]
            with mock.patch.object(tts_daemon, "CONFIG_PATH", config):
                with open(config, "w") as f:
                    json.dump({"muted": True, "moss_ref_audio": "/tmp/other.wav"}, f)
                stamp = os.path.getmtime(config) + 10
                os.utime(config, (stamp, stamp))
                engine.refresh_config()
            # 参考音频是加载时定死的，热改只会让配置和听到的声音对不上
            self.assertEqual(engine.cfg["moss_ref_audio"], loaded_voice)
            self.assertTrue(engine.cfg["muted"])


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
