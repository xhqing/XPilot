"""Health checking for proxy nodes."""

import json
import logging
import os
import signal
import subprocess
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class CheckError(Exception):
    """Health check error."""
    pass


class HealthChecker:
    """Check node health status."""

    TEST_URL = 'https://www.google.com/generate_204'

    # 真实流量探测时依次尝试的 URL：generate_204 端点只回一个 204 状态码、
    # 不传输正文，最轻量。google 主端点不通时退到 gstatic，再退到 cloudflare，
    # 避免单一端点临时抖动导致误判整组节点不可用。
    REAL_TEST_URLS = (
        'https://www.google.com/generate_204',
        'https://www.gstatic.com/generate_204',
        'http://cp.cloudflare.com/generate_204',
    )

    # 临时探测实例的端口区间（仅 bind 127.0.0.1，用完即释放）。
    PROBE_PORT_START = 11000
    PROBE_PORT_END = 12000

    def __init__(self, proxy_manager=None):
        self.proxy_manager = proxy_manager

    def check_latency(self, node: dict, timeout: int = 10) -> float:
        """Check node latency in milliseconds."""
        from .utils import ping_host
        latency = ping_host(node['address'], node['port'], timeout)
        return latency

    def check_connectivity(self, node: dict, url: str = None, timeout: int = 10) -> bool:
        """Check node connectivity via HTTP test."""
        from .utils import check_connectivity
        test_url = url or self.TEST_URL
        proxy_url = f'socks5://127.0.0.1:{self._get_socks_port()}' if self.proxy_manager else None
        return check_connectivity(test_url, proxy_url, timeout)

    def batch_check(self, node_ids: list = None) -> list:
        """Batch check multiple nodes."""
        if node_ids is None:
            from .node_manager import NodeManager
            # node_ids should be provided by caller
            return []

        results = []
        for node_id in node_ids:
            result = self._check_single_node(node_id)
            results.append(result)

        return results

    def _check_single_node(self, node_id: str) -> dict:
        """Check a single node and return result."""
        from .node_manager import NodeNotFoundError, NodeManager

        # We need access to node_manager
        result = {
            'id': node_id,
            'name': node_id,
            'latency': float('inf'),
            'connected': False,
            'error': None,
        }

        try:
            # Get node info from node_manager (set via cli)
            if hasattr(self, '_node_manager'):
                node = self._node_manager.get_node(node_id)
                result['name'] = node.get('name', node_id)

                latency = self.check_latency(node)
                result['latency'] = latency if latency != float('inf') else -1
                result['connected'] = latency != float('inf')

                # Update node latency in config
                if result['connected']:
                    updates = {
                        'latency': int(latency),
                        'last_check': datetime.now().isoformat(),
                        'status': 'active'
                    }
                    self._node_manager.update_node(node_id, updates)
            else:
                result['error'] = 'Node manager not available'

        except NodeNotFoundError:
            result['error'] = 'Node not found'
        except Exception as e:
            result['error'] = str(e)
            result['latency'] = -1

        return result

    def sort_by_latency(self, results: list) -> list:
        """Sort check results by latency."""
        valid = [r for r in results if r.get('latency', -1) > 0]
        invalid = [r for r in results if r.get('latency', -1) <= 0]
        valid.sort(key=lambda x: x['latency'])
        return valid + invalid

    def set_node_manager(self, node_manager) -> None:
        """Set node manager reference for node lookup."""
        self._node_manager = node_manager

    def _get_socks_port(self) -> int:
        """Get SOCKS proxy port from settings."""
        if self.proxy_manager and hasattr(self.proxy_manager, 'config'):
            try:
                settings = self.proxy_manager.config.load_config('settings.json')
                return settings.get('socks_port', 1080)
            except Exception:
                pass
        return 1080  # Default port

    # ==================== 真实流量检测 ====================
    #
    # 下面这套方法解决 check_latency 的根本盲区：TCP 握手成功既不保证
    # 代理协议握手成功（UUID/密码失效、加密方式不匹配都会被对端静默丢弃），
    # 也不保证对端真的能出网。做法是为节点单独起一个临时 xray 实例，用 curl
    # 经它访问 generate_204，把「TCP 可达」与「代理可用」彻底区分开。

    def _find_free_port(self, start=None, end=None):
        """在探测端口区间内找一个当前未被占用的端口（仅判断，不长期持有）。"""
        from .utils import is_port_available
        start = start or self.PROBE_PORT_START
        end = end or self.PROBE_PORT_END
        for port in range(start, end):
            if is_port_available(port):
                return port
        raise CheckError(f'无可用临时端口（{start}-{end}）')

    def _curl_through_socks(self, port, timeout):
        """走 socks5h 代理依次尝试 REAL_TEST_URLS。

        返回 (ok, http_code, latency_ms)。socks5h 让 DNS 也经代理解析，
        避免本地 DNS 污染把被墙域名解析到错误 IP、造成「代理本身能用却
        打不开 YouTube/Google」的误判。
        """
        for url in self.REAL_TEST_URLS:
            try:
                proc = subprocess.run(
                    ['curl', '-x', f'socks5h://127.0.0.1:{port}', '-sS',
                     '-o', '/dev/null', '-w', '%{http_code}|%{time_total}',
                     '--max-time', str(timeout), url],
                    capture_output=True, text=True,
                )
            except FileNotFoundError:
                raise CheckError('未找到 curl，真实流量检测依赖系统 curl')
            out = (proc.stdout or '').strip()
            if '|' in out:
                code_s, t_s = out.split('|', 1)
                code = int(code_s) if code_s.isdigit() else 0
                try:
                    latency_ms = int(float(t_s) * 1000)
                except ValueError:
                    latency_ms = None
                # generate_204 标准返回 204；个别端点返回 200/3xx 也算通。
                if code == 204 or 200 <= code < 400:
                    return True, code, latency_ms
        return False, 0, None

    def _start_probe(self, node, port=None):
        """为节点起一个临时 xray 探测实例（独立 socks 端口、仅绑 127.0.0.1）。

        成功时返回 ``{ok:True, proc, cfg_path, port, tcp_ms}``，调用方需在 finally
        里 ``_kill_proc(proc)`` 与 ``os.unlink(cfg_path)``。失败时返回
        ``{ok:False, error, tcp_ms}`` 且已自行清理，调用方无需再清理。
        """
        info = {'ok': False, 'proc': None, 'cfg_path': None,
                'port': port, 'tcp_ms': None, 'error': None}
        if not self.proxy_manager:
            info['error'] = 'proxy_manager 未注入，无法生成 xray 配置'
            return info
        try:
            settings = self.proxy_manager.config.load_config('settings.json')
        except Exception:
            settings = {}
        xray_bin = settings.get('xray_bin', '/usr/local/bin/xray')
        if not os.path.exists(xray_bin):
            info['error'] = f'xray 二进制不存在: {xray_bin}'
            return info

        try:
            tcp = self.check_latency(node, timeout=5)
            info['tcp_ms'] = int(tcp) if tcp != float('inf') else None
        except Exception:
            pass

        if port is None:
            try:
                port = self._find_free_port()
            except CheckError as e:
                info['error'] = str(e)
                return info
        info['port'] = port

        try:
            outbound = self.proxy_manager._generate_outbound(node, tag='proxy')
        except Exception as e:
            info['error'] = f'生成出站配置失败: {e}'
            return info
        cfg = {
            'log': {'loglevel': 'warning'},
            'inbounds': [{
                'port': port,
                'listen': '127.0.0.1',
                'protocol': 'socks',
                'settings': {'auth': 'noauth', 'udp': False},
                'sniffing': {'enabled': True, 'destOverride': ['http', 'tls']},
            }],
            'outbounds': [outbound,
                          {'tag': 'direct', 'protocol': 'freedom', 'settings': {}}],
        }
        cfg_path = f'/tmp/xpilot-probe-{os.getpid()}-{port}.json'
        proc = None
        try:
            with open(cfg_path, 'w') as f:
                json.dump(cfg, f)
            proc = subprocess.Popen(
                [xray_bin, 'run', '-config', cfg_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            # 给 xray 一点时间 bind 端口；节点出站握手耗时算在 curl 超时里。
            time.sleep(1.0)
            if proc.poll() is not None:
                err_lines = (proc.stderr.read().decode(errors='ignore').strip()
                             .splitlines() if proc.stderr else [])
                tail = err_lines[-1] if err_lines else '(无输出)'
                info['error'] = f'xray 启动即退出: {tail}'
                self._kill_proc(proc)
                try:
                    os.unlink(cfg_path)
                except OSError:
                    pass
                return info
            info['ok'] = True
            info['proc'] = proc
            info['cfg_path'] = cfg_path
            return info
        except Exception as e:
            info['error'] = f'探测实例启动异常: {e}'
            self._kill_proc(proc)
            try:
                os.unlink(cfg_path)
            except OSError:
                pass
            return info

    def check_real_traffic(self, node, timeout=10, port=None):
        """通过临时 xray 实例实测节点能否真正代理流量。

        返回 dict: {ok, http_code, latency_ms, tcp_ms, error}。其中 tcp_ms
        顺带记录一次 TCP 延迟，供 test 命令两列对照展示。
        """
        probe = self._start_probe(node, port=port)
        result = {'ok': False, 'http_code': 0, 'latency_ms': None,
                  'tcp_ms': probe.get('tcp_ms'), 'error': probe.get('error')}
        if not probe['ok']:
            return result
        try:
            ok, code, latency_ms = self._curl_through_socks(probe['port'], timeout)
            result['ok'] = ok
            result['http_code'] = code
            result['latency_ms'] = latency_ms
            if not ok:
                result['error'] = '代理流量不通（curl 全部失败或超时）'
        except CheckError as e:
            result['error'] = str(e)
        finally:
            self._kill_proc(probe.get('proc'))
            try:
                os.unlink(probe.get('cfg_path'))
            except (OSError, TypeError):
                pass
        return result

    # ==================== 网速测试 ====================
    #
    # 测速用 Cloudflare 的 __down 端点下载固定大小字节，按 curl 的
    # speed_download（bytes/s）换算 Mbps。该端点国内外均可访问，走代理测
    # 「经代理到国外 CDN」的速度、直连测「本地到公网」的速度。

    SPEED_TEST_URL = 'https://speed.cloudflare.com/__down?bytes={size}'

    def _curl_download(self, port, size_bytes, timeout, direct=False):
        """下载测速，返回 (speed_mbps, elapsed_s)，失败 (None, None)。

        direct=True 时不走代理（加 --noproxy '*' 忽略系统代理），测直连速度。
        """
        url = self.SPEED_TEST_URL.format(size=size_bytes)
        cmd = ['curl', '-sS', '-o', '/dev/null',
               '-w', '%{speed_download}|%{time_total}',
               '--max-time', str(timeout)]
        if direct:
            cmd += ['--noproxy', '*']
        else:
            cmd += ['-x', f'socks5h://127.0.0.1:{port}']
        cmd.append(url)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            raise CheckError('未找到 curl，测速依赖系统 curl')
        out = (proc.stdout or '').strip()
        if '|' in out:
            spd_s, t_s = out.split('|', 1)
            try:
                speed_bps = float(spd_s)            # bytes/s
                elapsed = float(t_s)
                return speed_bps * 8 / 1_000_000, elapsed   # Mbps
            except ValueError:
                pass
        return None, None

    def check_speed(self, node, size_bytes=10_000_000, timeout=60, port=None):
        """测节点经代理的下载速度。

        返回 dict: {speed_mbps, elapsed, tcp_ms, error}。
        """
        probe = self._start_probe(node, port=port)
        result = {'speed_mbps': None, 'elapsed': None,
                  'tcp_ms': probe.get('tcp_ms'), 'error': probe.get('error')}
        if not probe['ok']:
            return result
        try:
            speed, elapsed = self._curl_download(probe['port'], size_bytes, timeout)
            result['speed_mbps'] = speed
            result['elapsed'] = elapsed
            if speed is None:
                result['error'] = result['error'] or '下载测速失败（超时或连接失败）'
        except CheckError as e:
            result['error'] = str(e)
        finally:
            self._kill_proc(probe.get('proc'))
            try:
                os.unlink(probe.get('cfg_path'))
            except (OSError, TypeError):
                pass
        return result

    def check_direct_speed(self, size_bytes=10_000_000, timeout=60):
        """测直连下载速度（不走代理）。

        返回 dict: {speed_mbps, elapsed, error}。
        """
        result = {'speed_mbps': None, 'elapsed': None, 'error': None}
        try:
            speed, elapsed = self._curl_download(None, size_bytes, timeout, direct=True)
            result['speed_mbps'] = speed
            result['elapsed'] = elapsed
            if speed is None:
                result['error'] = '直连测速失败（超时或无法访问测速服务器）'
        except CheckError as e:
            result['error'] = str(e)
        return result

    def check_direct_latency(self, timeout=8):
        """直连（不走代理）访问 generate_204 测延迟，返回 latency_ms 或 None。

        用于和经节点延迟对比：直连延迟反映本地网络到公网的状况。墙内 google/
        gstatic 直连通常不通，会自动回退到 cloudflare 端点（仍能反映本地网络延迟）。
        """
        for url in self.REAL_TEST_URLS:
            try:
                proc = subprocess.run(
                    ['curl', '--noproxy', '*', '-sS', '-o', '/dev/null',
                     '-w', '%{http_code}|%{time_total}',
                     '--max-time', str(timeout), url],
                    capture_output=True, text=True,
                )
            except FileNotFoundError:
                raise CheckError('未找到 curl，直连延迟检测依赖系统 curl')
            out = (proc.stdout or '').strip()
            if '|' in out:
                code_s, t_s = out.split('|', 1)
                code = int(code_s) if code_s.isdigit() else 0
                if code == 204 or 200 <= code < 400:
                    try:
                        return int(float(t_s) * 1000)
                    except ValueError:
                        pass
        return None

    def batch_check_speed(self, node_ids, size_bytes=10_000_000, timeout=60, concurrency=6):
        """并发测多个节点经代理的下载速度，结果按 node_ids 顺序返回。"""
        if not getattr(self, '_node_manager', None):
            return []
        from .utils import is_port_available

        nodes = {}
        for nid in node_ids:
            try:
                nodes[nid] = self._node_manager.get_node(nid)
            except Exception:
                nodes[nid] = None

        ports = []
        candidate = self.PROBE_PORT_START
        for _ in node_ids:
            while candidate < self.PROBE_PORT_END and not is_port_available(candidate):
                candidate += 1
            if candidate >= self.PROBE_PORT_END:
                candidate = self.PROBE_PORT_START
            ports.append(candidate)
            candidate += 1

        def work(nid, port):
            node = nodes.get(nid)
            if not node:
                return nid, {'speed_mbps': None, 'elapsed': None, 'tcp_ms': None,
                             'error': '节点不存在', 'name': nid}
            r = self.check_speed(node, size_bytes=size_bytes, timeout=timeout, port=port)
            r['name'] = node.get('name', nid)
            return nid, r

        results = {}
        workers = min(concurrency, max(1, len(node_ids)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, nid, port) for nid, port in zip(node_ids, ports)]
            for fut in as_completed(futs):
                try:
                    nid, r = fut.result()
                    results[nid] = r
                except Exception as e:
                    logger.warning(f'测速任务异常: {e}')
        placeholder = {'speed_mbps': None, 'elapsed': None, 'tcp_ms': None, 'error': '未执行'}
        out = []
        for nid in node_ids:
            r = results[nid] if nid in results else dict(placeholder, name=nid)
            r['id'] = nid
            out.append(r)
        return out

    @staticmethod
    def _kill_proc(proc):
        """安全终止一个用 start_new_session=True 启动的子进程（杀整个进程组）。"""
        if proc is None:
            return
        try:
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                time.sleep(0.3)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass
        except Exception:
            pass

    def batch_check_real(self, node_ids, timeout=10, concurrency=6):
        """并发对多个节点做真实流量检测，结果按 node_ids 原顺序返回。

        端口在主线程预先分配（每节点一个），规避并发下 _find_free_port 的竞态。
        """
        if not getattr(self, '_node_manager', None):
            return []
        from .utils import is_port_available

        nodes = {}
        for nid in node_ids:
            try:
                nodes[nid] = self._node_manager.get_node(nid)
            except Exception:
                nodes[nid] = None

        ports = []
        candidate = self.PROBE_PORT_START
        for _ in node_ids:
            while candidate < self.PROBE_PORT_END and not is_port_available(candidate):
                candidate += 1
            if candidate >= self.PROBE_PORT_END:
                candidate = self.PROBE_PORT_START
            ports.append(candidate)
            candidate += 1

        def work(nid, port):
            node = nodes.get(nid)
            if not node:
                return nid, {'ok': False, 'http_code': 0, 'latency_ms': None,
                             'tcp_ms': None, 'error': '节点不存在', 'name': nid}
            r = self.check_real_traffic(node, timeout=timeout, port=port)
            r['name'] = node.get('name', nid)
            return nid, r

        results = {}
        workers = min(concurrency, max(1, len(node_ids)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, nid, port) for nid, port in zip(node_ids, ports)]
            for fut in as_completed(futs):
                try:
                    nid, r = fut.result()
                    results[nid] = r
                except Exception as e:
                    logger.warning(f'真实流量检测任务异常: {e}')
        placeholder = {'ok': False, 'http_code': 0, 'latency_ms': None,
                       'tcp_ms': None, 'error': '未执行'}
        out = []
        for nid in node_ids:
            r = results[nid] if nid in results else dict(placeholder, name=nid)
            r['id'] = nid
            out.append(r)
        return out
