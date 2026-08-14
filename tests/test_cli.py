"""Tests for CLI module (monitor daemon management)."""

import os
from types import SimpleNamespace

import pytest

from xpilot import cli


class TestKillAllMonitors:
    """monitor 守护进程清理测试。"""

    def test_kills_all_monitors_not_just_pid_file(self, monkeypatch, tmp_path):
        """测试：清理所有 monitor 进程（不只 PID 文件记录的一个）。

        多代 monitor 积累是 2026-08-14 的真实故障：旧 monitor 用旧代码
        反复写 xray 配置、拉起 xray，覆盖新配置导致改动不生效。
        """
        monkeypatch.setattr(cli, 'AUTO_SWITCH_PID_FILE', str(tmp_path / 'auto-switch.pid'))

        # 假 pgrep 输出：3 个 monitor 进程（PID 文件只记录了其中一个）
        fake_pids = '111\n222\n333\n'
        monkeypatch.setattr(
            cli.subprocess, 'run',
            lambda *a, **k: SimpleNamespace(stdout=fake_pids))

        killed = []
        monkeypatch.setattr(cli.os, 'kill', lambda pid, sig: killed.append(pid))

        # PID 文件存在（记录了 111）
        with open(cli.AUTO_SWITCH_PID_FILE, 'w') as f:
            f.write('111')

        cli._kill_all_monitors()

        # 3 个 monitor 都被 SIGTERM + SIGKILL 两轮覆盖
        assert set(killed) == {111, 222, 333}
        assert len(killed) == 6
        # PID 文件被清理
        assert not os.path.exists(cli.AUTO_SWITCH_PID_FILE)

    def test_no_monitors_no_error(self, monkeypatch, tmp_path):
        """测试：无 monitor 进程时不报错、不残留 PID 文件。"""
        monkeypatch.setattr(cli, 'AUTO_SWITCH_PID_FILE', str(tmp_path / 'auto-switch.pid'))
        monkeypatch.setattr(
            cli.subprocess, 'run',
            lambda *a, **k: SimpleNamespace(stdout=''))
        killed = []
        monkeypatch.setattr(cli.os, 'kill', lambda pid, sig: killed.append(pid))

        cli._kill_all_monitors()

        assert killed == []
        assert not os.path.exists(cli.AUTO_SWITCH_PID_FILE)

    def test_pgrep_failure_is_non_fatal(self, monkeypatch, tmp_path):
        """测试：pgrep 失败（如系统无 pgrep）时静默降级，不抛异常。"""
        monkeypatch.setattr(cli, 'AUTO_SWITCH_PID_FILE', str(tmp_path / 'auto-switch.pid'))
        monkeypatch.setattr(
            cli.subprocess, 'run',
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('pgrep missing')))

        cli._kill_all_monitors()  # 不应抛异常

    def test_does_not_kill_self(self, monkeypatch, tmp_path):
        """测试：不误杀调用者自身进程。"""
        monkeypatch.setattr(cli, 'AUTO_SWITCH_PID_FILE', str(tmp_path / 'auto-switch.pid'))
        self_pid = os.getpid()
        fake_pids = f'{self_pid}\n222\n'
        monkeypatch.setattr(
            cli.subprocess, 'run',
            lambda *a, **k: SimpleNamespace(stdout=fake_pids))
        killed = []
        monkeypatch.setattr(cli.os, 'kill', lambda pid, sig: killed.append(pid))

        cli._kill_all_monitors()

        assert self_pid not in killed
        assert 222 in killed
