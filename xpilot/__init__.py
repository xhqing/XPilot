"""xpilot - A CLI proxy toolkit with xray backend."""

import os
from importlib.metadata import PackageNotFoundError, version as _distribution_version

#: 仓库根的版本号文件（版本号的唯一权威源），相对于本文件向上两级。
_DEFAULT_VERSION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'VERSION')


def _resolve_version(distribution_version=None, version_file=None) -> str:
    """解析当前版本号，不做任何硬编码。

    版本号曾经写死在 ``__version__`` 常量里，而发版只改打包元数据——两者一旦
    不同步，就会出现「``pip show`` 说是 0.7.0、``--version`` 说 0.6.4」的错位
    （Issue #9），连带让 ``--update`` 每次都认为有新版本、重复下载安装同一个
    wheel。改成运行时解析可以彻底消掉这个会漏的载体：

    1. **已安装** → 打包元数据（``pyproject.toml`` / ``setup.py`` 里的
       ``version``，与 ``pip show`` 同源）——那两个文件本来就在发版清单里；
    2. **源码直跑**（包未安装）→ 仓库根的 ``VERSION`` 文件，它是版本号的唯一
       权威来源，同样在发版清单里；
    3. 两条都不可用 → ``'unknown'``。导入本包不能因为拿不到版本号而失败。

    ``distribution_version`` / ``version_file`` 是测试用的注入点，生产路径不传。
    """
    getter = distribution_version or (lambda: _distribution_version('xpilot'))
    try:
        resolved = getter()
    except PackageNotFoundError:
        resolved = None
    if resolved:
        return str(resolved).strip()

    path = version_file or _DEFAULT_VERSION_FILE
    try:
        with open(path, 'r') as f:
            content = f.read().strip()
    except OSError:
        return 'unknown'
    return content or 'unknown'


__version__ = _resolve_version()
