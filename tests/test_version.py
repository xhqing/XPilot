"""版本号解析（Issue #9 / T23 契约）。

0.7.0 发布后 `xpilot --version` 仍报 `0.6.4`：`xpilot/__init__.py` 里硬编码的
版本常量没跟着 bump 走——wheel 元数据是 0.7.0、源码常量是 0.6.4。更严重的是
`updater` 拿它当「当前版本」，于是每次 `--update` 都认为 0.6.4 < 0.7.0，
重复下载并重装同一个 wheel，静默无提示。

修法：不再硬编码，改为运行时解析——已安装时读打包元数据（`pip show` 的同一个
来源），源码直跑时读仓库根 `VERSION`，两者都不可用时返回 `'unknown'` 而不是
让 import 崩掉。本文件钉住三条路径、优先级与「不许再硬编码」。
"""

import importlib.metadata
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

import xpilot
from xpilot import _resolve_version, updater
from xpilot.cli import cli as cli_command

REPO_ROOT = Path(__file__).resolve().parents[1]
INIT_SOURCE = REPO_ROOT / 'xpilot' / '__init__.py'


def _missing_distribution():
    """模拟「源码直跑、包未安装」：打包元数据查不到。"""
    raise importlib.metadata.PackageNotFoundError('xpilot')


def _repo_version():
    return (REPO_ROOT / 'VERSION').read_text().strip()


def _distribution_version():
    """真实环境里已安装的版本；未安装返回 None。"""
    try:
        return importlib.metadata.version('xpilot')
    except importlib.metadata.PackageNotFoundError:
        return None


class TestInstalledPath:
    """契约 2.1：已安装 → 用打包元数据，且优先于 VERSION 文件。"""

    def test_returns_distribution_version(self):
        assert _resolve_version(distribution_version=lambda: '0.7.0') == '0.7.0'

    def test_distribution_wins_over_missing_version_file(self, tmp_path):
        """优先级正确：metadata 拿得到时不关心 VERSION 文件是否存在。"""
        missing = tmp_path / 'VERSION'

        assert _resolve_version(distribution_version=lambda: '1.2.3',
                                version_file=str(missing)) == '1.2.3'


class TestSourceCheckoutPath:
    """契约 2.2：未安装 → 读仓库根 VERSION，去空白后返回。"""

    @pytest.mark.parametrize('content,expected', [
        ('0.7.0\n', '0.7.0'),
        ('  0.7.0  \n', '0.7.0'),
        ('1.2.3', '1.2.3'),
    ])
    def test_reads_version_file(self, tmp_path, content, expected):
        version_file = tmp_path / 'VERSION'
        version_file.write_text(content)

        assert _resolve_version(distribution_version=_missing_distribution,
                                version_file=str(version_file)) == expected

    def test_default_version_file_is_repository_version(self):
        """不注入 version_file 时默认读仓库根 VERSION（源码直跑的真实形态）。"""
        assert _resolve_version(distribution_version=_missing_distribution) == _repo_version()


class TestUnknownFallback:
    """契约 2.3：两条都不可用 → 'unknown'，不抛异常。"""

    def test_unknown_when_version_file_missing(self, tmp_path):
        assert _resolve_version(distribution_version=_missing_distribution,
                                version_file=str(tmp_path / 'VERSION')) == 'unknown'

    @pytest.mark.parametrize('content', ['', '   \n', '\n\t '])
    def test_unknown_when_version_file_is_blank(self, tmp_path, content):
        version_file = tmp_path / 'VERSION'
        version_file.write_text(content)

        assert _resolve_version(distribution_version=_missing_distribution,
                                version_file=str(version_file)) == 'unknown'

    def test_unknown_when_version_file_unreadable(self, tmp_path):
        """读取失败（这里用目录制造 IsADirectoryError）同样降级为 unknown。"""
        version_file = tmp_path / 'VERSION'
        version_file.mkdir()

        assert _resolve_version(distribution_version=_missing_distribution,
                                version_file=str(version_file)) == 'unknown'


class TestNoHardcodedLiteral:
    """契约：__init__.py 不许再出现版本字面量赋值（本次事故的载体）。"""

    def test_no_version_literal_assignment(self):
        source = INIT_SOURCE.read_text()

        assert re.search(r"__version__\s*=\s*['\"]\d", source) is None, (
            'xpilot/__init__.py 又出现了硬编码版本赋值——版本号必须由 '
            '_resolve_version() 解析，否则 bump 会再次漏掉这里')

    def test_version_is_derived_from_resolver(self):
        source = INIT_SOURCE.read_text()

        assert re.search(r'__version__\s*=\s*_resolve_version\(\)', source), (
            '模块级 __version__ 必须来自 _resolve_version()')


class TestRealEnvironmentConsistency:
    """真实环境一致性：按「任一路径成立」写宽断言，避免 CI / 本地安装形态差异。"""

    def test_module_version_matches_repo_or_distribution(self):
        versions = {_repo_version(), _distribution_version()}
        versions.discard(None)

        assert xpilot.__version__ in versions

    def test_cli_version_reports_real_version(self):
        """用户可见的验收点：`xpilot --version` 不能再报陈旧版本。"""
        result = CliRunner().invoke(cli_command, ['--version'])

        assert result.exit_code == 0
        versions = {_repo_version(), _distribution_version()}
        versions.discard(None)
        assert any(version in result.output for version in versions), result.output

    def test_updater_uses_the_same_version(self):
        """update 链路的基准必须是真实版本，否则会重复下载安装同一个 wheel。"""
        versions = {_repo_version(), _distribution_version()}
        versions.discard(None)

        assert updater.get_current_version() in versions
