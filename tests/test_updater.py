"""Tests for the self-update module."""

import subprocess

import pytest

from xpilot import __version__ as CURRENT_VERSION
from xpilot.updater import (
    UpdateError,
    find_release_by_version,
    is_newer,
    parse_version,
    perform_rollback,
    perform_update,
    pick_install_target,
    pick_previous_release,
)


class TestParseVersion:
    def test_plain(self):
        """测试：纯数字版本号按点号拆分解析。"""
        assert parse_version('0.1.1') == (0, 1, 1)

    def test_v_prefix(self):
        """测试：带 v / V 前缀的版本号前缀被剥离。"""
        assert parse_version('v0.1.1') == (0, 1, 1)
        assert parse_version('V1.2.3') == (1, 2, 3)

    def test_suffix_is_stripped(self):
        """测试：带后缀（如 -beta）的版本号只取每段开头数字。"""
        assert parse_version('0.1.1-beta') == (0, 1, 1)

    def test_empty_and_none(self):
        """测试：空串与 None 返回空元组。"""
        assert parse_version('') == ()
        assert parse_version(None) == ()

    def test_non_numeric(self):
        """测试：纯非数字字符串退化为 (0,)。"""
        assert parse_version('abc') == (0,)


class TestIsNewer:
    def test_strictly_newer(self):
        """测试：更高版本号判为更新。"""
        assert is_newer('0.2.0', '0.1.1') is True

    def test_equal_is_not_newer(self):
        """测试：相同版本不判为更新。"""
        assert is_newer('0.1.1', '0.1.1') is False

    def test_older_is_not_newer(self):
        """测试：更低版本号不判为更新。"""
        assert is_newer('0.1.0', '0.1.1') is False

    def test_different_width_is_equal_not_newer(self):
        """测试：0.1 与 0.1.0 补零后视为相等，不会因长度不同误判为更新。"""
        assert is_newer('0.1', '0.1.0') is False
        assert is_newer('0.1.0', '0.1') is False

    def test_accepts_v_prefix(self):
        """测试：比较时容忍 v 前缀。"""
        assert is_newer('v0.1.2', '0.1.1') is True


class TestPickInstallTarget:
    def test_prefers_wheel_over_sdist(self):
        """测试：同时存在 wheel 与 tar.gz 时优先选 wheel。"""
        release = {'assets': [
            {'name': 'xpilot-0.1.1.tar.gz', 'browser_download_url': 'TGZ'},
            {'name': 'xpilot-0.1.1-py3-none-any.whl', 'browser_download_url': 'WHL'},
        ]}
        assert pick_install_target(release) == 'WHL'

    def test_falls_back_to_sdist(self):
        """测试：无 wheel 时回退到 sdist 压缩包。"""
        release = {'assets': [
            {'name': 'xpilot-0.1.1.tar.gz', 'browser_download_url': 'TGZ'},
        ]}
        assert pick_install_target(release) == 'TGZ'

    def test_empty_assets(self):
        """测试：空资产列表返回 None（调用方回退到 git 安装）。"""
        assert pick_install_target({'assets': []}) is None

    def test_missing_assets_key(self):
        """测试：Release 字典缺 assets 键时返回 None。"""
        assert pick_install_target({}) is None

    def test_none_release(self):
        """测试：传入 None 时返回 None，不抛异常。"""
        assert pick_install_target(None) is None


def _release(tag, assets=None):
    """构造一个最小可用 Release 字典用于注入测试。"""
    return {'tag_name': tag, 'assets': assets or []}


class TestPerformUpdate:
    def test_already_up_to_date_does_not_install(self):
        """测试：最新版与当前版相同时返回 False 且不触发 pip 安装。"""
        calls = []
        ok = perform_update(
            fetcher=lambda: _release('v' + CURRENT_VERSION),
            runner=lambda cmd: calls.append(cmd),
        )
        assert ok is False
        assert calls == []

    def test_installs_wheel_asset(self):
        """测试：有新版且 Release 含 wheel 时，把 wheel URL 交给 pip。"""
        captured = {}
        release = _release('v9.9.9', [
            {'name': 'xpilot-9.9.9-py3-none-any.whl', 'browser_download_url': 'http://w.whl'},
        ])
        ok = perform_update(
            fetcher=lambda: release,
            runner=lambda cmd: captured.setdefault('cmd', cmd),
        )
        assert ok is True
        assert 'http://w.whl' in captured['cmd']
        assert '-m' in captured['cmd']
        assert 'pip' in captured['cmd']

    def test_falls_back_to_git_tag_without_assets(self):
        """测试：新版无构建资产时，回退到 git+tag 源码安装。"""
        captured = {}
        ok = perform_update(
            fetcher=lambda: _release('v9.9.9'),
            runner=lambda cmd: captured.setdefault('cmd', cmd),
        )
        assert ok is True
        assert 'git+https://github.com/xhqing/xpilot@v9.9.9' in captured['cmd']

    def test_check_only_does_not_install(self):
        """测试：check_only 模式只报告有新版，不调用 pip。"""
        calls = []
        ok = perform_update(
            check_only=True,
            fetcher=lambda: _release('v9.9.9'),
            runner=lambda cmd: calls.append(cmd),
        )
        assert ok is True
        assert calls == []

    def test_fetch_failure_raises_update_error(self):
        """测试：取 Release 抛错时转成 UpdateError，而非裸异常暴露。"""
        def boom():
            raise UpdateError('network down')
        with pytest.raises(UpdateError):
            perform_update(fetcher=boom, runner=lambda cmd: None)

    def test_pip_failure_raises_update_error(self):
        """测试：pip 安装失败（非零退出）时转成 UpdateError。"""
        def fail(cmd):
            raise subprocess.CalledProcessError(1, cmd)
        with pytest.raises(UpdateError):
            perform_update(fetcher=lambda: _release('v9.9.9'), runner=fail)


class TestPickPreviousRelease:
    def test_returns_next_older_release(self):
        """测试：返回严格早于当前版本的最大一个版本。"""
        releases = [
            {'tag_name': 'v0.2.0'},
            {'tag_name': 'v0.1.1'},
            {'tag_name': 'v0.1.0'},
        ]
        prev = pick_previous_release(releases, '0.2.0')
        assert prev['tag_name'] == 'v0.1.1'

    def test_none_when_already_oldest(self):
        """测试：当前已是历史最低版时返回 None。"""
        releases = [{'tag_name': 'v0.1.0'}, {'tag_name': 'v0.1.1'}]
        assert pick_previous_release(releases, '0.1.0') is None

    def test_skips_releases_without_parseable_tag(self):
        """测试：缺 tag 或空 tag 的 Release 被跳过，不影响查找。"""
        releases = [{'tag_name': 'v0.2.0'}, {'no_tag': True}, {'tag_name': ''}]
        assert pick_previous_release(releases, '0.2.0') is None

    def test_empty_or_none_list(self):
        """测试：空列表或 None 返回 None。"""
        assert pick_previous_release([], '0.2.0') is None
        assert pick_previous_release(None, '0.2.0') is None


class TestFindReleaseByVersion:
    def test_finds_matching_tag(self):
        """测试：按版本号命中对应 Release。"""
        releases = [{'tag_name': 'v0.2.0'}, {'tag_name': 'v0.1.1'}]
        assert find_release_by_version(releases, '0.1.1')['tag_name'] == 'v0.1.1'

    def test_accepts_v_prefixed_query(self):
        """测试：查询串带 v 前缀也能命中。"""
        releases = [{'tag_name': 'v0.1.1'}]
        assert find_release_by_version(releases, 'v0.1.1')['tag_name'] == 'v0.1.1'

    def test_returns_none_when_missing(self):
        """测试：版本不存在时返回 None。"""
        assert find_release_by_version([{'tag_name': 'v0.2.0'}], '9.9.9') is None

    def test_empty_query_returns_none(self):
        """测试：空查询串返回 None。"""
        assert find_release_by_version([{'tag_name': 'v0.2.0'}], '') is None
        assert find_release_by_version([{'tag_name': 'v0.2.0'}], None) is None


class TestPerformRollback:
    def test_default_rolls_back_to_previous_release(self):
        """测试：不指定版本时回滚到前一个 Release 的 wheel 资产。"""
        captured = {}
        releases = [
            {'tag_name': 'v' + CURRENT_VERSION, 'assets': []},
            {'tag_name': 'v0.1.1', 'assets': [
                {'name': 'xpilot-0.1.1-py3-none-any.whl', 'browser_download_url': 'http://old.whl'},
            ]},
        ]
        ok = perform_rollback(
            fetcher=lambda: releases,
            runner=lambda cmd: captured.setdefault('cmd', cmd),
        )
        assert ok is True
        assert 'http://old.whl' in captured['cmd']

    def test_explicit_version_installs_that_release(self):
        """测试：指定版本时安装该版本，无资产则回退 git+tag 源码安装。"""
        captured = {}
        releases = [
            {'tag_name': 'v0.2.0'},
            {'tag_name': 'v0.1.1', 'assets': [
                {'name': 'xpilot-0.1.1.tar.gz', 'browser_download_url': 'http://old.tgz'},
            ]},
            {'tag_name': 'v0.1.0', 'assets': []},
        ]
        ok = perform_rollback(
            version='0.1.0',
            fetcher=lambda: releases,
            runner=lambda cmd: captured.setdefault('cmd', cmd),
        )
        assert ok is True
        assert 'git+https://github.com/xhqing/xpilot@v0.1.0' in captured['cmd']

    def test_already_at_target_version_does_nothing(self):
        """测试：目标版本与当前版本相同时不调用 pip。"""
        calls = []
        ok = perform_rollback(
            version=CURRENT_VERSION,
            fetcher=lambda: [{'tag_name': 'v' + CURRENT_VERSION}],
            runner=lambda cmd: calls.append(cmd),
        )
        assert ok is False
        assert calls == []

    def test_no_previous_release_raises(self):
        """测试：没有更早 Release 可回滚时抛 UpdateError。"""
        with pytest.raises(UpdateError):
            perform_rollback(
                fetcher=lambda: [{'tag_name': 'v' + CURRENT_VERSION}],
                runner=lambda cmd: None,
            )

    def test_unknown_version_raises(self):
        """测试：指定的版本不存在时抛 UpdateError。"""
        with pytest.raises(UpdateError):
            perform_rollback(
                version='9.9.9',
                fetcher=lambda: [{'tag_name': 'v0.2.0'}],
                runner=lambda cmd: None,
            )

    def test_fetch_failure_raises_update_error(self):
        """测试：取 Release 列表抛错时转成 UpdateError。"""
        def boom():
            raise UpdateError('network down')
        with pytest.raises(UpdateError):
            perform_rollback(fetcher=boom, runner=lambda cmd: None)
