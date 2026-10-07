#!/usr/bin/env python3
"""Conservative Linux IPv4 ASM membership keeper. Python 3.8+, stdlib only."""
import argparse
import configparser
import ctypes
import fcntl
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import select
import signal
import socket
import struct
import sys
import time

VERSION = '0.1.1'
DEFAULTS = {
    'interfaces': {'upstream': 'auto', 'downstream': 'auto'},
    'igmp': {'force_version': 'auto', 'query_downstream': 'fallback',
             'stale_group_seconds': '300', 'query_evidence_seconds': '400',
             'traffic_grace_seconds': '90', 'fallback_after_seconds': '150'},
    'general': {'refresh_interval': 'auto', 'scan_interval': '5',
                'heartbeat_interval': '3600',
                'log_path': '/data/iptv-igmp-keeper/keeper.log',
                'state_path': '/run/iptv-igmp-keeper/status.json'},
    'clients': {'macs': 'auto'},
}
LOG = logging.getLogger('iptv-igmp-keeper')


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def asm_group(group):
    address = ipaddress.IPv4Address(group)
    return (address.is_multicast and address not in ipaddress.IPv4Network('224.0.0.0/24')
            and address not in ipaddress.IPv4Network('232.0.0.0/8'))


def read_config(path=None):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict(DEFAULTS)
    if path:
        with open(path, encoding='utf-8') as stream:
            cfg.read_file(stream)
    for section in cfg.sections():
        if section not in DEFAULTS or set(cfg[section]) - set(DEFAULTS[section]):
            raise ValueError('Unknown configuration section/key: ' + section)
    for key, choices in [('force_version', ('auto', 'v2', 'off')),
                         ('query_downstream', ('fallback', 'off'))]:
        if cfg['igmp'][key] not in choices:
            raise ValueError('Invalid ' + key)
    for section, key, low, high in [
        ('general', 'scan_interval', 1, 30),
        ('general', 'heartbeat_interval', 60, 86400),
        ('igmp', 'stale_group_seconds', 60, 3600),
        ('igmp', 'query_evidence_seconds', 60, 3600),
        ('igmp', 'traffic_grace_seconds', 5, 300),
        ('igmp', 'fallback_after_seconds', 30, 600),
    ]:
        value = cfg.getfloat(section, key)
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError('%s must be between %s and %s' % (key, low, high))
    if cfg['general']['refresh_interval'] != 'auto':
        value = cfg.getfloat('general', 'refresh_interval')
        if not math.isfinite(value) or not 10 <= value <= 120:
            raise ValueError('refresh_interval must be auto or 10..120 seconds')
    for key in ('upstream', 'downstream'):
        names = cfg['interfaces'][key].split(',')
        if any(not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', name) for name in names):
            raise ValueError('Invalid interface name(s); comma separate without spaces')
        if key == 'upstream' and len(names) != 1:
            raise ValueError('Only one upstream supported')
    macs = cfg['clients']['macs']
    if macs != 'auto' and any(not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', m)
                             for m in macs.split(',')):
        raise ValueError('Invalid clients.macs')
    if cfg.getfloat('igmp', 'fallback_after_seconds') + 10 >= cfg.getfloat('igmp', 'stale_group_seconds'):
        raise ValueError('fallback_after_seconds must be at least 10s below stale_group_seconds')
    return cfg


def proc_address(value):
    return socket.inet_ntoa(struct.pack('=I', int(value, 16)))


def parse_tables(vif_text, cache_text):
    vifs, routes = {}, []
    for line in vif_text.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            vifs[int(fields[0])] = fields[1]
    for line in cache_text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 6:
            continue
        outputs = [int(item.split(':')[0]) for item in fields[6:] if ':' in item]
        routes.append({'group': proc_address(fields[0]), 'source': proc_address(fields[1]),
                       'iif': int(fields[2]), 'packets': int(fields[3]), 'oifs': outputs})
    return vifs, routes


def topology(vifs, routes, cfg):
    explicit = cfg['interfaces']['upstream']
    parents = {vifs[r['iif']] for r in routes if r['iif'] in vifs and r['oifs']}
    upstream = explicit if explicit != 'auto' else (next(iter(parents)) if len(parents) == 1 else None)
    downstream = cfg['interfaces']['downstream']
    if downstream == 'auto':
        downstream = sorted({vifs[o] for r in routes if vifs.get(r['iif']) == upstream
                             for o in r['oifs'] if o in vifs and vifs[o] != upstream})
    else:
        downstream = downstream.split(',')
    if upstream in downstream:
        return None, [], 'upstream overlaps downstream'
    reason = 'ready' if upstream and downstream else 'waiting for unambiguous multicast routes or overrides'
    return upstream, downstream, reason


def discover(cfg, proc=Path('/proc/net')):
    try:
        vifs, routes = parse_tables((proc / 'ip_mr_vif').read_text(), (proc / 'ip_mr_cache').read_text())
        upstream, downstream, reason = topology(vifs, routes, cfg)
        return {'upstream': upstream, 'downstream': downstream, 'reason': reason,
                'vifs': vifs, 'routes': routes}
    except (OSError, ValueError) as exc:
        return {'upstream': None, 'downstream': [], 'vifs': {}, 'routes': [],
                'reason': 'multicast tables unavailable: ' + str(exc)}


def refresh_interval(cfg, downstream):
    if cfg['general']['refresh_interval'] != 'auto':
        return cfg.getfloat('general', 'refresh_interval')
    intervals = []
    for name in downstream:
        try:
            ticks = int(Path('/sys/class/net', name, 'bridge/multicast_membership_interval').read_text())
            if ticks > 0:
                intervals.append(ticks / os.sysconf('SC_CLK_TCK'))
        except (OSError, ValueError):
            pass
    return max(10, min(60, min(intervals or [260]) / 4))


def parse_packet(packet):
    """Parse network-layer IPv4 IGMP; reject truncation, fragments and bad checksums."""
    if len(packet) < 28 or packet[0] >> 4 != 4:
        return None
    ihl, total = (packet[0] & 15) * 4, struct.unpack_from('!H', packet, 2)[0]
    if ihl < 20 or total < ihl + 8 or total > len(packet) or packet[9] != 2 or packet[8] != 1:
        return None
    if struct.unpack_from('!H', packet, 6)[0] & 0x3fff or checksum(packet[:ihl]):
        return None
    data = packet[ihl:total]
    if checksum(data):
        return None
    kind, code = data[:2]
    group = socket.inet_ntoa(data[4:8])
    if kind == 0x11:
        if len(data) == 8:
            return ('query', 1 if code == 0 else 2, group, code / 10 if code else 10)
        if len(data) >= 12 and len(data) == 12 + 4 * struct.unpack_from('!H', data, 10)[0]:
            response = code if code < 128 else ((code & 15) | 16) << (((code >> 4) & 7) + 3)
            return ('query', 3, group, response / 10)
    elif kind in (0x12, 0x16, 0x17) and len(data) == 8:
        return ('records', [('leave' if kind == 0x17 else 'asm', group)])
    elif kind == 0x22:
        records, offset = [], 8
        for _ in range(struct.unpack_from('!H', data, 6)[0]):
            if offset + 8 > len(data):
                return None
            record_type, aux, count = struct.unpack_from('!BBH', data, offset)
            group = socket.inet_ntoa(data[offset + 4:offset + 8])
            offset += 8 + 4 * count + 4 * aux
            if offset > len(data) or record_type not in range(1, 7):
                return None
            if record_type in (5, 6) and count == 0:
                continue  # Empty ALLOW/BLOCK deltas carry no source-filter change.
            # Only EXCLUDE {} is unambiguously ASM. Never emulate source filters.
            mode = ('asm' if record_type in (2, 4) and count == 0 else
                    'leave' if record_type in (1, 3) and count == 0 else 'source-filter')
            records.append((mode, group))
        if offset == len(data):
            return ('records', records)
    return None


class Memberships:
    def __init__(self):
        self.clients = {}  # (interface, MAC, group) -> (mode, monotonic timestamp)
        self.queries = {}  # (interface, version) -> timestamp
        self.query_deadlines = {}  # (interface, group) -> response deadline

    def observe(self, interface, mac, event, now, allowed=None):
        if event[0] == 'query':
            self.queries[(interface, event[1])] = now
            response_seconds = event[3] if len(event) > 3 else 10
            self.query_deadlines[(interface, event[2])] = now + response_seconds
            return
        for mode, group in event[1]:
            address = ipaddress.IPv4Address(group)
            if not address.is_multicast or address in ipaddress.IPv4Network('224.0.0.0/24'):
                # Local control groups (e.g. mDNS) are not IPTV/SSM evidence.
                continue
            key = (interface, mac, group)
            if mode == 'leave':
                self.clients.pop(key, None)
            elif mode == 'source-filter' or not asm_group(group):
                # Safety evidence is retained even for clients outside the allowlist.
                self.clients[key] = ('source-filter', now)
            elif allowed is None or mac in allowed:
                self.clients[key] = (mode, now)

    def expire(self, now, stale, evidence):
        self.clients = {k: v for k, v in self.clients.items() if now - v[1] < stale}
        self.queries = {k: v for k, v in self.queries.items() if now - v < evidence}
        self.query_deadlines = {k: v for k, v in self.query_deadlines.items() if now < v}

    def fallback_groups(self, interface, groups, now, delay, started):
        """Query presence is not proof of working membership renewal.

        Check each routed group separately: another channel's Reports must not
        hide a silent group. Give external queries their advertised response
        window, but allow recovery after that window if Reports remain absent.
        """
        due = []
        for group in sorted(groups):
            if not asm_group(group):
                continue
            newest = max([stamp for (name, _, subscribed), (_, stamp) in self.clients.items()
                          if name == interface and subscribed == group] or [started])
            deadline = max(self.query_deadlines.get((interface, '0.0.0.0'), 0),
                           self.query_deadlines.get((interface, group), 0))
            if now - newest >= delay and now >= deadline:
                due.append(group)
        return due

    def permit(self, upstream, mode):
        if mode == 'off':
            return False, 'force_version=off (observe only)'
        if any(value[0] == 'source-filter' for value in self.clients.values()):
            return False, 'source-filter/SSM evidence present'
        if any((upstream, version) in self.queries for version in (1, 3)):
            return False, 'upstream v1/v3 query evidence present'
        if mode == 'v2' or (upstream, 2) in self.queries:
            return True, 'v2 explicitly configured' if mode == 'v2' else 'upstream v2 query observed'
        return False, 'waiting for upstream v2 query'


def interface_identity(name):
    """Include IPv4 and MAC to notice address/reprovision changes as well as ifindex."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        request = struct.pack('256s', name.encode())
        address = socket.inet_ntoa(fcntl.ioctl(sock, 0x8915, request)[20:24])
    return (socket.if_nametoindex(name), address,
            Path('/sys/class/net', name, 'address').read_text().strip())


def capture_socket(name):
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800))
    try:
        sock.bind((name, 0))
        # Classic BPF on SOCK_DGRAM AF_PACKET starts at the IPv4 header.
        # Discard video traffic in kernel rather than copying it into Python.
        class Filter(ctypes.Structure):
            _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
                        ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]
        class Program(ctypes.Structure):
            _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]
        instructions = (Filter * 4)(Filter(0x30, 0, 0, 9), Filter(0x15, 0, 1, 2),
                                    Filter(0x06, 0, 0, 65535), Filter(0x06, 0, 0, 0))
        program = Program(4, instructions)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.setsockopt(sock.fileno(), socket.SOL_SOCKET, 26,
                           ctypes.byref(program), ctypes.sizeof(program)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def send_igmp(interface, address, group, query=False):
    destination = '224.0.0.1' if query else group
    body = struct.pack('!BBH4s', 0x11 if query else 0x16, 50 if query else 0, 0,
                       socket.inet_aton('0.0.0.0' if query else group))
    body = body[:2] + struct.pack('!H', checksum(body)) + body[4:]
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IGMP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_OPTIONS, b'\x94\x04\x00\x00')  # Router Alert
        sock.bind((address, 0))
        sock.sendto(body, (destination, 0))


class VersionOverride:
    """Restore only values still owned by us, including after an unclean restart."""
    def __init__(self, journal, dry=False):
        self.path, self.dry = Path(journal), dry
        self.saved = json.loads(self.path.read_text()) if self.path.exists() and not dry else {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.saved))
        temporary.replace(self.path)

    def restore(self):
        if self.dry:
            return
        for name, entry in list(self.saved.items()):
            path = Path('/proc/sys/net/ipv4/conf', name, 'force_igmp_version')
            try:
                identity = interface_identity(name)
                same_device = (identity[0], identity[2]) == (entry['identity'][0], entry['identity'][2])
                if same_device and path.read_text().strip() == '2':
                    path.write_text(entry['value'] + '\n')
                del self.saved[name]
            except (OSError, ValueError):
                if not path.exists():
                    del self.saved[name]
                else:
                    LOG.exception('Could not restore IGMP version on %s', name)
        self.save()

    def apply(self, name, identity):
        if self.dry:
            return
        path = Path('/proc/sys/net/ipv4/conf', name, 'force_igmp_version')
        value = path.read_text().strip()
        if value == '2':
            return
        if name not in self.saved:
            self.saved[name] = {'identity': identity, 'value': value}
            self.save()  # Journal BEFORE mutation, kept under /run for this boot.
        path.write_text('2\n')


class Keeper:
    def __init__(self, cfg, dry=False):
        self.cfg, self.dry = cfg, dry
        self.members = Memberships()
        self.sockets, self.identities = {}, {}
        self.previous_counters, self.traffic = {}, {}
        self.last_query, self.last_refresh = {}, {}
        self.reports_sent, self.queries_sent = 0, 0
        self.last_sent_report, self.last_sent_query = {}, {}
        self.started = time.monotonic()
        self.last_heartbeat = self.started
        self.signature = None
        self.running = True
        self.snapshot = {}
        self.overrides = VersionOverride(Path(cfg['general']['state_path']).with_name('overrides.json'), dry)
        self.overrides.restore()
        macs = cfg['clients']['macs']
        self.allowed = None if macs == 'auto' else set(macs.lower().split(','))

    def reconcile(self, info):
        names = ([info['upstream']] + info['downstream']) if info['upstream'] else []
        identities = {}
        for name in names:
            identities[name] = interface_identity(name)
        if identities != self.identities:
            self.overrides.restore()
            for sock in self.sockets.values():
                sock.close()
            self.sockets = {}
            self.identities = {}
            self.members = Memberships()
            self.previous_counters.clear()
            self.traffic.clear()
            self.last_query.clear()
            self.last_refresh.clear()
            self.last_sent_report.clear()
            self.last_sent_query.clear()
            self.started = time.monotonic()
            try:
                for name in names:
                    self.sockets[name] = capture_socket(name)
            except OSError:
                for sock in self.sockets.values():
                    sock.close()
                self.sockets = {}
                raise
            self.identities = identities
            LOG.info('Interfaces changed: %s', identities)

    def poll(self, timeout):
        ready, _, _ = select.select(list(self.sockets.values()), [], [], timeout)
        by_socket = {sock: name for name, sock in self.sockets.items()}
        for sock in ready:
            for _ in range(128):  # Bound processing to keep timers responsive.
                try:
                    packet, address = sock.recvfrom(65535)
                except BlockingIOError:
                    break
                if address[2] == socket.PACKET_OUTGOING:
                    continue
                event = parse_packet(packet)
                if event is None:
                    continue
                name = by_socket[sock]
                if event[0] != 'query' and name == self.snapshot.get('upstream'):
                    continue
                mac = ':'.join('%02x' % byte for byte in address[4][:6])
                if mac == self.identities[name][2]:
                    continue
                self.members.observe(name, mac, event, time.monotonic(), self.allowed)

    def scan(self):
        now = time.monotonic()
        info = discover(self.cfg)
        self.reconcile(info)
        self.members.expire(now, self.cfg.getfloat('igmp', 'stale_group_seconds'),
                            self.cfg.getfloat('igmp', 'query_evidence_seconds'))
        upstream, downstream = info['upstream'], info['downstream']
        interval = refresh_interval(self.cfg, downstream)
        permit, reason = self.members.permit(upstream, self.cfg['igmp']['force_version'])
        permit = permit and bool(upstream and downstream)
        if info['reason'] != 'ready':
            reason = info['reason']
        counters, routed = {}, set()
        for route in info['routes']:
            if info['vifs'].get(route['iif']) != upstream:
                continue
            group = route['group']
            key = (route['source'], group, route['iif'], tuple(route['oifs']))
            counters[key] = route['packets']
            relevant = {info['vifs'].get(o) for o in route['oifs']} & set(downstream)
            if (key in self.previous_counters and route['packets'] > self.previous_counters[key]):
                for name in relevant:
                    self.traffic[(name, group)] = now
            routed.update((name, group) for name in relevant)
        self.previous_counters = counters
        grace = self.cfg.getfloat('igmp', 'traffic_grace_seconds')
        self.traffic = {key: stamp for key, stamp in self.traffic.items()
                        if key in routed and now - stamp < grace}
        active = sorted({group for (name, _, group), (mode, _) in self.members.clients.items()
                         if mode == 'asm' and asm_group(group) and (name, group) in self.traffic})
        if permit:
            self.overrides.apply(upstream, self.identities[upstream])
            for group in active:
                if now - self.last_refresh.get(group, -1e9) >= interval:
                    if not self.dry:
                        send_igmp(upstream, self.identities[upstream][1], group)
                        self.reports_sent += 1
                        self.last_sent_report[group] = now
                    self.last_refresh[group] = now
            if self.cfg['igmp']['query_downstream'] == 'fallback':
                delay = self.cfg.getfloat('igmp', 'fallback_after_seconds')
                for name in downstream:
                    groups = {group for iface, group in self.traffic if iface == name}
                    due = self.members.fallback_groups(name, groups, now, delay, self.started)
                    if due and now - self.last_query.get(name, self.started) >= delay:
                        if not self.dry:
                            send_igmp(name, self.identities[name][1], '0.0.0.0', query=True)
                            self.queries_sent += 1
                            self.last_sent_query[name] = now
                        self.last_query[name] = now
                        LOG.info('%sGeneral Query fallback on %s; stale/missing reports: %s',
                                 'Would send ' if self.dry else '', name, ', '.join(due))
        else:
            self.overrides.restore()
        self.last_refresh = {g: t for g, t in self.last_refresh.items() if g in active}
        # Retain recent send evidence long enough to diagnose a stopped stream.
        self.last_sent_report = {g: t for g, t in self.last_sent_report.items() if now - t < 3600}
        membership_ages = {}
        for (name, _, group), (mode, stamp) in self.members.clients.items():
            key = (name, group, mode)
            membership_ages[key] = min(membership_ages.get(key, float('inf')), now - stamp)
        self.snapshot = {'version': VERSION, 'pid': os.getpid(), 'updated_at': time.time(),
                         'dry_run': self.dry, 'upstream': upstream, 'downstream': downstream,
                         'enabled': permit, 'reason': reason, 'refresh_interval': interval,
                         'active_groups': active, 'client_memberships': len(self.members.clients),
                         'source_filter_groups': sorted({group for (_, _, group), (mode, _)
                                                         in self.members.clients.items() if mode == 'source-filter'}),
                         'reports_sent': self.reports_sent, 'fallback_queries_sent': self.queries_sent,
                         'last_report_age_seconds': {g: round(now - t, 1)
                                                     for g, t in sorted(self.last_sent_report.items())},
                         'last_fallback_query_age_seconds': {n: round(now - t, 1)
                                                             for n, t in sorted(self.last_sent_query.items())},
                         'memberships': [{'interface': n, 'group': g, 'mode': mode,
                                          'report_age_seconds': round(age, 1),
                                          'recent_traffic': (n, g) in self.traffic}
                                         for (n, g, mode), age in sorted(membership_ages.items())],
                         'queries': [{'interface': n, 'version': v, 'age_seconds': round(now - t, 1)}
                                     for (n, v), t in sorted(self.members.queries.items())]}
        signature = (upstream, tuple(downstream), permit, reason, tuple(active),
                     tuple(self.snapshot['source_filter_groups']))
        if signature != self.signature:
            LOG.info('State: %s', json.dumps(self.snapshot, sort_keys=True))
            self.signature = signature
        if now - self.last_heartbeat >= self.cfg.getfloat('general', 'heartbeat_interval'):
            LOG.info('Heartbeat: %s', json.dumps(self.snapshot, sort_keys=True))
            self.last_heartbeat = now
        if not self.dry:
            path = Path(self.cfg['general']['state_path'])
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.snapshot, indent=2) + '\n')
            temporary.replace(path)

    def close(self):
        self.overrides.restore()
        for sock in self.sockets.values():
            sock.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='INI file (omitted: built-in defaults)')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--discover', action='store_true', help='read-only interface/route discovery')
    actions.add_argument('--status', action='store_true', help='read last daemon status')
    actions.add_argument('--check-config', action='store_true', help='validate config and exit')
    parser.add_argument('--once', action='store_true', help='observe for --observe-seconds, then exit')
    parser.add_argument('--observe-seconds', type=float, default=15)
    parser.add_argument('--dry-run', action='store_true', help='observe; never transmit, write files or sysctls')
    parser.add_argument('--version', action='version', version=VERSION)
    args = parser.parse_args(argv)
    try:
        cfg = read_config(args.config)
        if not math.isfinite(args.observe_seconds) or args.observe_seconds <= 0:
            raise ValueError('observe-seconds must be finite and positive')
        if args.check_config:
            print('Configuration OK')
            return 0
        if args.discover:
            print(json.dumps(discover(cfg), indent=2))
            return 0
        if args.status:
            path = Path(cfg['general']['state_path'])
            if not path.exists():
                print(json.dumps({'running': False, 'reason': 'no daemon status file'}))
                return 1
            state = json.loads(path.read_text())
            state['age_seconds'] = round(time.time() - state['updated_at'], 1)
            state['fresh'] = 0 <= state['age_seconds'] < cfg.getfloat('general', 'scan_interval') * 3
            print(json.dumps(state, indent=2))
            return 0 if state['fresh'] else 1
        LOG.setLevel(logging.INFO)
        if args.dry_run:
            handler = logging.StreamHandler()
        else:
            Path(cfg['general']['log_path']).parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(cfg['general']['log_path'], maxBytes=512 * 1024, backupCount=2)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        LOG.addHandler(handler)
        lock = None
        if not args.dry_run:
            state_path = Path(cfg['general']['state_path'])
            state_path.parent.mkdir(parents=True, exist_ok=True)
            lock = state_path.with_suffix('.lock').open('w')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        keeper = Keeper(cfg, args.dry_run)
        def stop(_signum, _frame):
            keeper.running = False
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        deadline = time.monotonic() + args.observe_seconds if args.once else float('inf')
        next_scan, failed, previous_error = 0, False, None
        try:
            while keeper.running:
                now = time.monotonic()
                if now >= next_scan or now >= deadline:
                    try:
                        keeper.scan()
                        failed, previous_error = False, None
                    except (OSError, ValueError) as exc:
                        keeper.overrides.restore()
                        failed = True
                        if str(exc) != previous_error:
                            LOG.exception('Scan failed; retrying: %s', exc)
                            previous_error = str(exc)
                    next_scan = now + cfg.getfloat('general', 'scan_interval')
                if now >= deadline:
                    break
                keeper.poll(min(1, max(0, next_scan - time.monotonic())))
        finally:
            keeper.close()
            if lock:
                lock.close()
        if args.once:
            print(json.dumps(keeper.snapshot, indent=2))
        return 1 if failed else 0
    except (OSError, ValueError, configparser.Error) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
