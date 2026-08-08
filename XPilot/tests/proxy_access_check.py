#!/usr/bin/env python3
"""Manual proxy reachability check — NOT a pytest test case.

This script is meant to be run directly after starting the proxy, to verify
that traffic actually flows through SOCKS5/HTTP inbounds to the target sites::

    python tests/proxy_access_check.py
    # or with custom host/ports:
    PROXY_HOST=127.0.0.1 SOCKS_PORT=1080 HTTP_PORT=1087 python tests/proxy_access_check.py

It is intentionally named without a ``test_`` prefix so pytest does not collect
it: its functions take a plain ``url`` argument for positional use from
``main()``, not a pytest fixture, which previously caused spurious "fixture
'url' not found" errors during ``pytest`` collection.
"""

import sys
import os
import json
import time
import requests


PROXY_HOST = os.getenv('PROXY_HOST', '127.0.0.1')
SOCKS_PORT = int(os.getenv('SOCKS_PORT', '1080'))
HTTP_PORT = int(os.getenv('HTTP_PORT', '1087'))

TEST_URLS = [
    ('YouTube', 'https://www.youtube.com'),
    ('YouTube API', 'https://www.googleapis.com/youtube/v3/videos'),
    ('Google', 'https://www.google.com'),
    ('Cloudflare', 'https://www.cloudflare.com'),
]


def test_direct_connection(url, timeout=10):
    """Test direct connection without proxy."""
    try:
        resp = requests.get(url, timeout=timeout)
        return True, resp.status_code, resp.elapsed.total_seconds()
    except Exception as e:
        return False, None, str(e)


def test_socks5_proxy(url, timeout=10):
    """Test connection through SOCKS5 proxy."""
    proxies = {
        'http': f'socks5h://{PROXY_HOST}:{SOCKS_PORT}',
        'https': f'socks5h://{PROXY_HOST}:{SOCKS_PORT}',
    }
    try:
        resp = requests.get(url, proxies=proxies, timeout=timeout)
        return True, resp.status_code, resp.elapsed.total_seconds()
    except Exception as e:
        return False, None, str(e)


def test_http_proxy(url, timeout=10):
    """Test connection through HTTP proxy."""
    proxies = {
        'http': f'http://{PROXY_HOST}:{HTTP_PORT}',
        'https': f'http://{PROXY_HOST}:{HTTP_PORT}',
    }
    try:
        resp = requests.get(url, proxies=proxies, timeout=timeout)
        return True, resp.status_code, resp.elapsed.total_seconds()
    except Exception as e:
        return False, None, str(e)


def print_result(name, success, status_code, time_or_error, indent=2):
    """Print test result."""
    prefix = '  ' * indent
    if success:
        print(f'{prefix}[OK] {name}: {status_code} ({time_or_error:.2f}s)')
    else:
        print(f'{prefix}[FAIL] {name}: {time_or_error}')


def main():
    print('=' * 60)
    print('Proxy Access Test')
    print(f'SOCKS5: {PROXY_HOST}:{SOCKS_PORT}')
    print(f'HTTP: {PROXY_HOST}:{HTTP_PORT}')
    print('=' * 60)

    results = {
        'direct': {},
        'socks5': {},
        'http': {},
    }

    for name, url in TEST_URLS:
        print(f'\n{name}: {url}')
        print('-' * 40)

        # Test direct
        ok, status, info = test_direct_connection(url)
        results['direct'][name] = ok
        print_result(f'Direct', ok, status, info)

        # Test SOCKS5
        ok, status, info = test_socks5_proxy(url)
        results['socks5'][name] = ok
        print_result(f'SOCKS5', ok, status, info)

        # Test HTTP proxy
        ok, status, info = test_http_proxy(url)
        results['http'][name] = ok
        print_result(f'HTTP', ok, status, info)

    # Summary
    print('\n' + '=' * 60)
    print('Summary')
    print('=' * 60)

    total = len(TEST_URLS)
    for mode in ['direct', 'socks5', 'http']:
        passed = sum(1 for v in results[mode].values() if v)
        status = 'PASS' if passed == total else 'FAIL'
        print(f'{mode.upper():8s}: {passed}/{total} ({status})')

    # YouTube specific check
    yt_socks = results['socks5'].get('YouTube', False)
    yt_http = results['http'].get('YouTube', False)
    print('\n' + '-' * 60)
    if yt_socks or yt_http:
        print('YouTube access via proxy: SUCCESS')
        return 0
    else:
        print('YouTube access via proxy: FAILED')
        print('  - Verify proxy server is running')
        print('  - Verify proxy config is correct')
        return 1


if __name__ == '__main__':
    sys.exit(main())
