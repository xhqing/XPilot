"""清理 0.6.3 及更早版本遗留在临时目录里的运行状态文件（Issue #5）。

0.6.4 之前，PID 文件、xray 运行配置与日志硬编码在 ``/tmp``。升级后新代码
一律读 ``<配置目录>/run``，旧文件既不读也不删——它们是惰性的，但当有人
在事故之后检查机器时极具误导性：``/tmp/xpilot.pid`` 摆在那里，看起来像
「旧实例还在跑」，而真相是它描述的那个进程早就没了。

所以本模块只做一件事：把已知名字的旧文件认出来、确认它们描述的进程确实
不在了，再删掉并记日志。名字清单是白名单而不是通配一切——``/tmp`` 是公共
目录，宁可漏删也不能误删别人的文件。
"""

import glob
import logging
import os

logger = logging.getLogger(__name__)

#: 旧版本的 PID 文件：内容是一个整数 PID，删之前先确认进程已死。
LEGACY_PID_FILES = ('xpilot.pid', 'xpilot-auto-switch.pid')

#: 旧版本固定名字的状态文件，无进程语义，直接删。
LEGACY_FILES = ('xpilot-xray.json', 'xpilot.log')

#: 旧版本 xray 日志的文件名模板（stdout / stderr 带 PID 与端口后缀）。
LEGACY_LOG_GLOBS = ('xpilot-xray-*.log',)

DEFAULT_TMP_DIR = '/tmp'


def _default_pid_alive(pid: int) -> bool:
    """PID 对应的进程是否还活着。

    ``os.kill(pid, 0)`` 只做存在性检查、不发信号：``ProcessLookupError``
    说明进程没了，``PermissionError`` 说明进程存在但属于别人（同样算活着），
    其它 ``OSError`` 一律按「不确定是否存活」保守处理为已死——删一个已经
    没用的 PID 文件不会造成损失，留着误导才会。
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _default_log(level: str, message: str) -> None:
    """默认日志出口：按级别转发到模块 logger。"""
    if level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        logger.info(message)


def find_legacy_paths(tmp_dir: str = DEFAULT_TMP_DIR) -> list:
    """列出 ``tmp_dir`` 下实际存在的遗留状态文件（去重、按路径排序）。

    只认 :data:`LEGACY_PID_FILES` / :data:`LEGACY_FILES` 里的固定名字，以及
    :data:`LEGACY_LOG_GLOBS` 匹配到的日志文件；不存在的不返回。
    """
    found = set()

    for name in LEGACY_PID_FILES + LEGACY_FILES:
        path = os.path.join(tmp_dir, name)
        if os.path.exists(path):
            found.add(path)

    for pattern in LEGACY_LOG_GLOBS:
        for path in glob.glob(os.path.join(tmp_dir, pattern)):
            if os.path.exists(path):
                found.add(path)

    return sorted(found)


def _read_pid(path: str):
    """读取 PID 文件内容；不是合法整数（含空文件 / 读取失败）时返回 ``None``。"""
    try:
        with open(path, 'r') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def cleanup_legacy_state(tmp_dir: str = DEFAULT_TMP_DIR, *, pid_alive=None,
                         log=None) -> list:
    """删除遗留状态文件，返回实际删除的路径列表。

    行为约定：

    * PID 文件描述的进程**仍然存活** → 跳过不删，记一条 warning。删掉活进程
      的 PID 文件反而会制造「工具不知道它在跑」的状态错位，正是 Issue #1 的
      病根。
    * PID 文件内容不是合法整数（空 / 乱写）→ 视为没有活进程，照删。
    * 单个文件删除失败 → 记 warning、继续处理其余文件、不向上抛、不计入返回值。
    * 幂等：没有遗留文件时返回空列表。

    ``pid_alive`` 与 ``log`` 都是可注入的：测试用假实现，生产走默认值。
    ``log`` 的签名固定为 ``log(level, message)``，``level`` 取 ``'info'`` /
    ``'warning'`` / ``'error'``。
    """
    pid_alive = pid_alive or _default_pid_alive
    log = log or _default_log

    removed = []
    for path in find_legacy_paths(tmp_dir):
        name = os.path.basename(path)
        if name in LEGACY_PID_FILES:
            pid = _read_pid(path)
            if pid is not None and pid_alive(pid):
                log('warning', f'遗留状态文件 {path} 描述的进程 {pid} 仍在运行，跳过删除')
                continue
        try:
            os.remove(path)
        except OSError as e:
            log('warning', f'删除遗留状态文件 {path} 失败: {e}')
            continue
        removed.append(path)

    if removed:
        log('info', f'已清理 {len(removed)} 个旧版本遗留状态文件：{", ".join(removed)}')
    return removed
