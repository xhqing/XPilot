"""Tests for subscription parsing —— VLESS+Reality 解析与 fragment 解码。"""

from xpilot.subscription import _parse_vless_link


class TestSubscription:
    def test_parse_vless_reality_link(self):
        """VLESS+Reality 链接应解析出全部 reality 参数，且 fragment 被 URL 解码。"""
        link = ('vless://aabbccdd-1234-5678-90ab-cdef01234567@1.2.3.4:443'
                '?encryption=none&flow=xtls-rprx-vision&security=reality'
                '&sni=portal.example.com&fp=chrome'
                '&pbk=PubKeyABC&sid=deadbeef&type=tcp'
                '#JMS-1336028%40c56s3.example.com%3A443')
        node = _parse_vless_link(link)
        assert node is not None
        assert node['protocol'] == 'vless'
        assert node['security'] == 'reality'
        assert node['tls'] is True
        assert node['servername'] == 'portal.example.com'
        assert node['reality_public_key'] == 'PubKeyABC'
        assert node['reality_short_id'] == 'deadbeef'
        assert node['fingerprint'] == 'chrome'
        assert node['flow'] == 'xtls-rprx-vision'
        # fragment 解码：%40 -> @, %3A -> :
        assert node['name'] == 'JMS-1336028@c56s3.example.com:443'

    def test_parse_vless_plain(self):
        """无 reality 的普通 vless 链接，reality 字段应为 None。"""
        link = 'vless://aabbccdd-1234-5678-90ab-cdef01234567@1.2.3.4:443?type=tcp&security=none#Plain'
        node = _parse_vless_link(link)
        assert node['security'] == 'none'
        assert node['tls'] is False
        assert node['reality_public_key'] is None
        assert node['flow'] is None
        assert node['name'] == 'Plain'

    def test_parse_ss_sip002_link(self):
        """SIP002 格式 SS 链接 base64(method:password)@host:port 应正确解析。

        回归测试：旧实现用 urlparse，userinfo 里的 base64 字符（+ / =）会让
        urlparse 把 userinfo 当成 hostname/port，导致 SS 节点解析失败、被静默跳过。
        """
        import base64 as _b64
        from xpilot.subscription import _parse_ss_link
        userinfo = _b64.b64encode(b'aes-256-gcm:s3cret-pwd').decode()
        link = f'{userinfo}@1.2.3.4:8388#MySS'
        link = 'ss://' + link
        node = _parse_ss_link(link)
        assert node is not None
        assert node['protocol'] == 'ss'
        assert node['address'] == '1.2.3.4'
        assert node['port'] == 8388
        assert node['security'] == 'aes-256-gcm'
        assert node['password'] == 's3cret-pwd'
        assert node['name'] == 'MySS'
