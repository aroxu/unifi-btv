import importlib.util
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('keeper', Path(__file__).resolve().parents[1] / 'src/iptv-igmp-keeper.py')
k = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(k)


def packet(body, options=b'', fragment=0):
    body = body[:2] + struct.pack('!H', k.checksum(body)) + body[4:]
    header = struct.pack('!BBHHHBBH4s4s', 0x45 + len(options) // 4, 0, 20 + len(options) + len(body),
                         0, fragment, 1, 2, 0, socket.inet_aton('192.0.2.1'), socket.inet_aton('224.0.0.1')) + options
    header = header[:10] + struct.pack('!H', k.checksum(header)) + header[12:]
    return header + body


def record(kind, group='239.1.2.3', count=0):
    return struct.pack('!BBH4s', kind, 0, count, socket.inet_aton(group)) + socket.inet_aton('192.0.2.1') * count


class ProtocolTests(unittest.TestCase):
    def test_queries_and_options(self):
        for code, tail, version in [(0, b'', 1), (50, b'', 2), (50, b'\x02\x7d\0\0', 3)]:
            data = packet(struct.pack('!BBH4s', 0x11, code, 0, b'\0' * 4) + tail, b'\x94\x04\0\0')
            self.assertEqual(k.parse_packet(data), ('query', version, '0.0.0.0'))

    def test_invalid_packets(self):
        data = packet(b'\x16\0\0\0' + socket.inet_aton('239.1.2.3'))
        for length in range(len(data)):
            self.assertIsNone(k.parse_packet(data[:length]))
        self.assertIsNone(k.parse_packet(data[:-1] + bytes([data[-1] ^ 1])))
        self.assertIsNone(k.parse_packet(packet(b'\x16\0\0\0' + socket.inet_aton('239.1.2.3'), fragment=0x2000)))

    def test_v3_source_filters(self):
        body = b'\x22\0\0\0\0\0\0\x03' + record(2) + record(1, count=1) + record(3)
        self.assertEqual(k.parse_packet(packet(body)), ('records', [
            ('asm', '239.1.2.3'), ('source-filter', '239.1.2.3'), ('leave', '239.1.2.3')]))
        self.assertIsNone(k.parse_packet(packet(body[:-1])))

    def test_ssm_and_control_groups(self):
        for group in ['232.1.2.3', '224.0.0.1', '192.0.2.1']:
            self.assertFalse(k.asm_group(group))
        self.assertTrue(k.asm_group('239.1.2.3'))

    def test_sender_wire_properties(self):
        with patch.object(k.socket, 'socket') as factory:
            sock = factory.return_value.__enter__.return_value
            k.send_igmp('wan0', '192.0.2.1', '239.1.2.3')
            body, destination = sock.sendto.call_args[0]
            self.assertEqual(destination, ('239.1.2.3', 0))
            self.assertEqual(k.checksum(body), 0)
            self.assertEqual(body[0], 0x16)
            sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_OPTIONS, b'\x94\x04\0\0')


class PolicyTests(unittest.TestCase):
    def test_multiple_clients_leave_and_expiry(self):
        m = k.Memberships()
        for mac in ['a', 'b']:
            m.observe('lan', mac, ('records', [('asm', '239.1.2.3')]), 10)
        m.observe('lan', 'a', ('records', [('leave', '239.1.2.3')]), 11)
        self.assertEqual(len(m.clients), 1)
        m.expire(311, 300, 400)
        self.assertFalse(m.clients)

    def test_auto_needs_v2_and_v3_blocks(self):
        m = k.Memberships()
        self.assertFalse(m.permit('wan', 'auto')[0])
        m.observe('wan', '', ('query', 2, '0.0.0.0'), 1)
        self.assertTrue(m.permit('wan', 'auto')[0])
        self.assertFalse(m.permit('wan', 'off')[0])
        m.observe('wan', '', ('query', 3, '0.0.0.0'), 2)
        self.assertFalse(m.permit('wan', 'auto')[0])
        self.assertFalse(m.permit('wan', 'v2')[0])
        m.expire(500, 300, 400)
        self.assertFalse(m.permit('wan', 'auto')[0])

    def test_filtered_client_still_blocks_source_filters(self):
        m = k.Memberships()
        m.observe('lan', 'unlisted', ('records', [('source-filter', '239.1.2.3')]), 1, {'listed'})
        self.assertFalse(m.permit('wan', 'v2')[0])

    def test_route_detection_and_ambiguity(self):
        vifs, routes = k.parse_tables('Interface BytesIn PktsIn BytesOut PktsOut Flags Local Remote\n0 wan 0 0\n1 lan 0 0\n',
                                     'Group Origin Iif Pkts Bytes Wrong Oifs\n030201EF 010200C0 0 42 0 0 1:1\n')
        if __import__('sys').byteorder == 'little':
            self.assertEqual(routes[0]['group'], '239.1.2.3')
        self.assertEqual(k.topology(vifs, routes, k.read_config())[:2], ('wan', ['lan']))
        routes.append(dict(routes[0], iif=1, oifs=[0]))
        self.assertIsNone(k.topology(vifs, routes, k.read_config())[0])

    def test_interval_and_bad_config(self):
        cfg = k.read_config()
        with patch.object(Path, 'read_text', return_value='8000'), patch.object(k.os, 'sysconf', return_value=100):
            self.assertEqual(k.refresh_interval(cfg, ['lan']), 20)
        with patch.object(Path, 'read_text', return_value='100000'):
            self.assertEqual(k.refresh_interval(cfg, ['lan']), 60)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            path.write_text('[general]\nrefresh_interval = nan\n')
            with self.assertRaises(ValueError):
                k.read_config(path)
            path.write_text('[igmp]\nforce_versions = v2\n')
            with self.assertRaises(ValueError):
                k.read_config(path)

    def test_dry_run_has_no_mutation(self):
        cfg = k.read_config()
        info = {'upstream': 'wan', 'downstream': ['lan'], 'reason': 'ready', 'vifs': {0: 'wan', 1: 'lan'},
                'routes': [{'source': '192.0.2.1', 'group': '239.1.2.3', 'iif': 0, 'packets': 1, 'oifs': [1]}]}
        with patch.object(k, 'discover', return_value=info), patch.object(k.Keeper, 'reconcile'), \
                patch.object(k, 'send_igmp') as send, patch.object(Path, 'write_text') as write:
            keeper = k.Keeper(cfg, dry=True)
            keeper.identities = {'wan': (1, '192.0.2.1', 'aa'), 'lan': (2, '192.0.2.2', 'bb')}
            now = k.time.monotonic()
            keeper.members.observe('wan', '', ('query', 2, '0.0.0.0'), now)
            keeper.members.observe('lan', 'a', ('records', [('asm', '239.1.2.3')]), now)
            keeper.scan()
            self.assertEqual(keeper.snapshot['active_groups'], [])
            info['routes'][0]['packets'] = 2
            keeper.scan()
            self.assertEqual(keeper.snapshot['active_groups'], ['239.1.2.3'])
            send.assert_not_called()
            write.assert_not_called()
            keeper.close()


class LifecycleTests(unittest.TestCase):
    def make_info(self):
        return {'upstream': 'wan', 'downstream': ['lan'], 'reason': 'ready', 'vifs': {0: 'wan', 1: 'lan'},
                'routes': [{'source': '192.0.2.1', 'group': '239.1.2.3', 'iif': 0, 'packets': 2, 'oifs': [1]}]}

    def test_fallback_no_external_querier_and_rate_limit(self):
        cfg = k.read_config()
        cfg['igmp']['force_version'] = 'v2'
        info = self.make_info()
        with patch.object(k, 'discover', return_value=info), patch.object(k.Keeper, 'reconcile'), \
                patch.object(k.time, 'monotonic', return_value=1000):
            keeper = k.Keeper(cfg, dry=True)
            keeper.started = 800
            keeper.identities = {'wan': (1, '192.0.2.1', 'aa'), 'lan': (2, '192.0.2.2', 'bb')}
            keeper.previous_counters = {('192.0.2.1', '239.1.2.3', 0, (1,)): 1}
            keeper.scan()
            self.assertEqual(keeper.last_query, {'lan': 1000})
            keeper.scan()
            self.assertEqual(keeper.last_query, {'lan': 1000})
            keeper.last_query.clear()
            keeper.members.observe('lan', '', ('query', 3, '0.0.0.0'), 990)
            keeper.scan()
            self.assertFalse(keeper.last_query)

    def test_counter_reset_and_expired_traffic_stop_refresh(self):
        cfg = k.read_config()
        cfg['igmp']['force_version'] = 'v2'
        info = self.make_info()
        with patch.object(k, 'discover', return_value=info), patch.object(k.Keeper, 'reconcile'), \
                patch.object(k.time, 'monotonic', return_value=1000):
            keeper = k.Keeper(cfg, dry=True)
            keeper.identities = {'wan': (1, '192.0.2.1', 'aa'), 'lan': (2, '192.0.2.2', 'bb')}
            keeper.previous_counters = {('192.0.2.1', '239.1.2.3', 0, (1,)): 100}
            keeper.members.observe('lan', 'a', ('records', [('asm', '239.1.2.3')]), 999)
            keeper.traffic[('lan', '239.1.2.3')] = 900
            keeper.scan()
            self.assertEqual(keeper.snapshot['active_groups'], [])

    def test_reprovision_resets_evidence(self):
        cfg = k.read_config()
        info = self.make_info()
        with patch.object(k, 'capture_socket') as capture, \
                patch.object(k, 'interface_identity', side_effect=lambda n: (10 if n == 'wan' else 20, '192.0.2.1', n)):
            keeper = k.Keeper(cfg, dry=True)
            keeper.members.observe('wan', '', ('query', 2, '0.0.0.0'), 1)
            keeper.members.observe('lan', 'a', ('records', [('asm', '239.1.2.3')]), 1)
            keeper.reconcile(info)
            self.assertFalse(keeper.members.clients)
            self.assertFalse(keeper.members.queries)
            self.assertEqual(capture.call_count, 2)
            keeper.close()

    def test_restore_same_interface_after_address_change(self):
        override = k.VersionOverride('/nonexistent/keeper-test-overrides.json', dry=True)
        override.dry = False
        override.saved = {'wan': {'identity': [1, '192.0.2.1', 'aa'], 'value': '0'}}
        with patch.object(k, 'interface_identity', return_value=(1, '192.0.2.2', 'aa')), \
                patch.object(Path, 'read_text', return_value='2'), patch.object(Path, 'write_text') as write, \
                patch.object(override, 'save'):
            override.restore()
            write.assert_called_once_with('0\n')
            self.assertFalse(override.saved)

    def test_restore_does_not_touch_recreated_interface_or_external_value(self):
        for identity, current in [((9, '192.0.2.1', 'aa'), '2'), ((1, '192.0.2.1', 'aa'), '3')]:
            override = k.VersionOverride('/nonexistent/keeper-test-overrides.json', dry=True)
            override.dry = False
            override.saved = {'wan': {'identity': [1, '192.0.2.1', 'aa'], 'value': '0'}}
            with patch.object(k, 'interface_identity', return_value=identity), \
                    patch.object(Path, 'read_text', return_value=current), patch.object(Path, 'write_text') as write, \
                    patch.object(override, 'save'):
                override.restore()
                write.assert_not_called()

    def test_override_journal_precedes_mutation(self):
        override = k.VersionOverride('/nonexistent/keeper-test-overrides.json', dry=True)
        override.dry = False
        events = []
        with patch.object(Path, 'read_text', return_value='0'), \
                patch.object(override, 'save', side_effect=lambda: events.append('journal')), \
                patch.object(Path, 'write_text', side_effect=lambda text: events.append(text)):
            override.apply('wan', (1, '192.0.2.1', 'aa'))
        self.assertEqual(events, ['journal', '2\n'])


if __name__ == '__main__':
    unittest.main()
