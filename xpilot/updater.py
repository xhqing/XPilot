"""xpilot 自更新模块：从最新的 GitHub Release 升级到新版本。

发布工作流（``.github/workflows/release.yml``）在打 tag 时会用
``python -m build`` 构建 wheel 与 sdist，并作为 Release 资产（assets）上传。
本模块即从这些资产安装——xpilot 只通过 GitHub Release 分发、并未上传 PyPI，
因此更新源必须指向 GitHub Release，而不是 ``pip install --upgrade xpilot``。

模块刻意把「取最新 Release / 比较版本 / 选资产 / 调 pip 安装」拆成独立函数，
既便于在 :mod:`xpilot.cli` 里编排，也便于在 :mod:`tests.test_updater` 中
用注入的假依赖（fetcher / runner）覆盖各分支，而不必真的联网或调用 pip。
"""

import subprocess
import sys

from . import __version__

REPO_OWNER = 'xhqing'
REPO_NAME = 'xpilot'
PROJECT_URL = f'https://github.com/{REPO_OWNER}/{REPO_NAME}'
LATEST_RELEASE_API = f'https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest'
ALL_RELEASES_API = f'https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases'


class UpdateError(Exception):
    """自更新过程中出现的、可向用户直接解释的错误。"""


def parse_version(version):
    """把版本字符串解析成可比较大小的整数元组。

    兼容 ``0.1.1``、``v0.1.1``、``V0.1.1`` 等写法，也容错形如 ``0.1.1-beta``
    的后缀——只取每一段开头的连续数字，遇到非数字即止。例如 ``'v0.1.1'``
    解析为 ``(0, 1, 1)``，``'1.2'`` 解析为 ``(1, 2)``。

    空串或 ``None`` 返回空元组 ``()``，调用方据此判断无法解析。
    """
    cleaned = (version or '').strip().lstrip('vV')
    if not cleaned:
        return ()
    parts = []
    for segment in cleaned.split('.'):
        digits = ''
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest, current):
    """判断 ``latest`` 版本是否严格高于 ``current``。

    先把两边的版本元组补零到相同长度再比较，避免 ``(0, 1)`` 与 ``(0, 1, 0)``
    因长度不同被 Python 判成不相等（短元组会被视为「更小」），从而误判
    「0.1 比 0.1.0 新」。
    """
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    width = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (width - len(latest_parts))
    current_parts += (0,) * (width - len(current_parts))
    return latest_parts > current_parts


def get_current_version():
    """返回当前运行的 xpilot 版本号（取自 ``xpilot.__version__``）。"""
    return __version__


def fetch_latest_release(timeout=15):
    """向 GitHub API 请求最新 Release 信息，返回其 JSON 字典。

    网络、HTTP、解析任一环节出错时统一抛 :class:`UpdateError`，由上层转成
    友好提示，而不是让原始 requests 异常直接暴露给用户。
    """
    import requests
    try:
        resp = requests.get(
            LATEST_RELEASE_API,
            timeout=timeout,
            headers={'Accept': 'application/vnd.github+json'},
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise UpdateError(f'获取最新 Release 失败：{e}') from e
    except ValueError as e:
        raise UpdateError(f'解析最新 Release 响应失败：{e}') from e


def fetch_releases(timeout=15):
    """向 GitHub API 请求全部 Release 列表（按发布时间倒序），返回 JSON 列表。

    回滚需要遍历历史 Release（取「上一版」或按版本号定位），故拉取全部列表而
    非 ``/releases/latest``。``per_page=100`` 一次取够，覆盖本项目可预见的发布
    数量；网络、HTTP、解析任一环节出错统一抛 :class:`UpdateError`，由上层转成
    友好提示。
    """
    import requests
    try:
        resp = requests.get(
            ALL_RELEASES_API,
            timeout=timeout,
            headers={'Accept': 'application/vnd.github+json'},
            params={'per_page': 100},
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise UpdateError(f'获取 Release 列表失败：{e}') from e
    except ValueError as e:
        raise UpdateError(f'解析 Release 列表响应失败：{e}') from e


def pick_install_target(release):
    """从 Release 资产里挑出可被 pip 直接安装的产物 URL。

    优先 wheel（安装最快、不依赖本机构建环境），其次 sdist 的 tar.gz /
    tar.bz2；两者都没有时返回 :data:`None`，调用方据此回退到从 git tag
    安装。
    """
    assets = (release or {}).get('assets') or []
    for asset in assets:
        if (asset.get('name') or '').endswith('.whl'):
            return asset.get('browser_download_url')
    for asset in assets:
        name = asset.get('name') or ''
        if name.endswith('.tar.gz') or name.endswith('.tar.bz2'):
            return asset.get('browser_download_url')
    return None


def _default_runner(cmd):
    """默认的 pip 执行器：复用当前解释器对应的 pip，失败即抛异常。"""
    return subprocess.run(cmd, check=True)


def _install_release(release, run, tell):
    """从给定 Release 选出资产并用 pip 安装其对应版本。

    被 :func:`perform_update` 与 :func:`perform_rollback` 共用：优先安装 Release
    附带的 wheel / sdist 资产（pip 直接安装文件 URL 会覆盖当前已装版本，故既能
    升级也能降级），两者都没有时回退到 ``git+<url>@<tag_name>`` 源码安装——
    tag_name 直接取自 Release 数据，与仓库实际 tag 名保持一致。
    """
    tag = (release or {}).get('tag_name') or ''
    target = pick_install_target(release)
    if target:
        tell(f'从 Release 资产安装：{target}')
    else:
        target = f'git+{PROJECT_URL}@{tag}'
        tell(f'未找到 Release 资产，改从源码安装：{target}')

    cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', '--', target]
    tell(f'执行命令：{" ".join(cmd)}')
    try:
        run(cmd)
    except subprocess.CalledProcessError as e:
        raise UpdateError(f'pip 安装失败，退出码 {e.returncode}') from e
    except FileNotFoundError as e:
        raise UpdateError('当前 Python 环境找不到 pip，无法完成安装。') from e


def perform_update(*, check_only=False, fetcher=None, runner=None, printer=None):
    """检查并执行自更新。

    :param check_only: 仅检查是否有新版、不真正安装。
    :param fetcher: 获取最新 Release 字典的可调用对象（测试注入用），默认走真实网络。
    :param runner: 执行 pip 命令的可调用对象（测试注入用），默认真实调用 pip。
    :param printer: 接收字符串、负责向用户展示进度的回调（测试注入用），默认静默。
    :return: 有新版（已装或因 check_only 待装）返回 True；已是最新返回 False。
    :raises UpdateError: 获取 Release 或安装失败时抛出。
    """
    tell = printer or (lambda *_args, **_kw: None)
    fetch = fetcher or fetch_latest_release
    run = runner or _default_runner

    current = get_current_version()
    tell(f'当前版本：{current}')
    tell(f'正在查询最新 Release：{PROJECT_URL} ...')

    release = fetch()
    tag = (release or {}).get('tag_name') or ''
    if not tag:
        raise UpdateError('最新 Release 缺少 tag_name，无法判断版本号。')
    latest = tag.lstrip('vV')
    tell(f'最新版本：{tag}')

    if not is_newer(latest, current):
        tell(f'已是最新版本（{current}）。')
        return False

    tell(f'发现新版本：{latest}')

    if check_only:
        tell('如需安装，请单独执行：xpilot --update')
        return True

    _install_release(release, run, tell)

    tell(f'更新完成，已升级到 {latest}。')
    return True


def pick_previous_release(releases, current):
    """从 Release 列表里挑出严格早于 ``current`` 的最近一个版本。

    用于无 ``--version`` 参数时的默认回滚目标：遍历所有 Release 的 tag，保留
    版本号严格小于当前版本的候选，再取其中最大的（即最接近当前版本的旧版本）。
    tag 缺失或解析结果为空的 Release 被跳过；找不到任何更旧的版本（当前已是
    历史最低版）时返回 :data:`None`。
    """
    candidates = []
    for release in releases or []:
        tag = (release or {}).get('tag_name') or ''
        if not tag:
            continue
        parsed = parse_version(tag.lstrip('vV'))
        if not parsed:
            continue
        # is_newer(current, tag) 为 True 即 current 严格新于该 tag，才算「更旧」。
        if is_newer(current, tag):
            candidates.append((parsed, release))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def find_release_by_version(releases, version):
    """从 Release 列表里找出与指定版本号匹配的 Release，找不到返回 :data:`None`。

    比较时把 tag 与 ``version`` 都去掉 ``v`` / ``V`` 前缀再逐一比对，故传
    ``0.1.1`` 或 ``v0.1.1`` 均可命中 ``tag_name`` 为 ``v0.1.1`` 的 Release。
    """
    want = (version or '').strip().lstrip('vV')
    if not want:
        return None
    for release in releases or []:
        tag = ((release or {}).get('tag_name') or '').strip().lstrip('vV')
        if tag == want:
            return release
    return None


def perform_rollback(version=None, *, fetcher=None, runner=None, printer=None):
    """回滚到指定版本；未指定版本时回滚到当前版本的前一个发布版本。

    :param version: 目标版本号（如 ``'0.1.1'``，可带 ``v`` 前缀）；为 :data:`None`
        时自动取严格早于当前版本的最近一个 Release。
    :param fetcher: 取 Release 列表的可调用对象（测试注入用），默认走真实网络。
    :param runner: 执行 pip 命令的可调用对象（测试注入用），默认真实调用 pip。
    :param printer: 接收字符串、负责向用户展示进度的回调（测试注入用），默认静默。
    :return: 成功降级到目标版本返回 True；已在目标版本无需操作时返回 False。
    :raises UpdateError: 获取列表、定位目标版本或安装失败时抛出。
    """
    tell = printer or (lambda *_args, **_kw: None)
    fetch = fetcher or fetch_releases
    run = runner or _default_runner

    current = get_current_version()
    tell(f'当前版本：{current}')
    tell(f'正在查询已发布的 Release：{PROJECT_URL} ...')

    releases = fetch()

    if version:
        release = find_release_by_version(releases, version)
        if not release:
            raise UpdateError(f'未找到版本 {version} 的 Release，请确认版本号。')
    else:
        release = pick_previous_release(releases, current)
        if not release:
            raise UpdateError('没有比当前版本更早的 Release，无法回滚。')

    target = (release.get('tag_name') or '').lstrip('vV')
    tell(f'目标版本：{target}')

    # 版本号双向都不「更新」即视为同一版本，避免 '0.1' 与 '0.1.0' 误判为不同。
    if not is_newer(target, current) and not is_newer(current, target):
        tell(f'已经是版本 {current}，无需回滚。')
        return False

    tell(f'即将回滚到 {target} ...')
    _install_release(release, run, tell)
    tell(f'回滚完成，已降级到 {target}。')
    return True
