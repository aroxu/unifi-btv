"""Exercise the piped bootstrap without installing or modifying gateway state."""
import io
import os
from pathlib import Path
import shutil
import shlex
import sys
import runpy
import subprocess
import tarfile
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
PAYLOAD = ['src/unifi-btv.py', 'config/config.example.ini',
           'uninstall.sh', 'unifi/50-unifi-btv.sh', 'install.sh']


@unittest.skipUnless(shutil.which('bash') and shutil.which('tar'), 'bash and tar required')
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.archive = self.root / 'source.tar.gz'
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.destination = self.root / 'checkout'
        self.marker = self.root / 'service-called'
        self.environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                                TMPDIR=str(self.scratch), KEEPER_TEST_ARCHIVE=str(self.archive),
                                KEEPER_TEST_MARKER=str(self.marker))
        self.make_command('curl', '''#!/bin/sh
set -eu
[ "${KEEPER_TEST_CURL_FAIL:-0}" = 0 ] || exit 22
for arg do
    case "$arg" in
        https://*) [ "$arg" = 'https://codeload.github.com/aroxu/unifi-btv/tar.gz/refs/heads/main' ] || exit 23 ;;
    esac
done
for arg do destination=$arg; done
cp "$KEEPER_TEST_ARCHIVE" "$destination"
''')
        # If any bootstrap regression attempts service operations, fail the test.
        for command in ('systemctl', 'systemd-run'):
            self.make_command(command, '#!/bin/sh\ntouch "$KEEPER_TEST_MARKER"\nexit 99\n')
        self.build_archive()

    def make_command(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def build_archive(self, omit=None, invalid_hook=False):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for relative in PAYLOAD:
                if relative == omit:
                    continue
                data = (BASE / relative).read_bytes()
                if invalid_hook and relative == 'unifi/50-unifi-btv.sh':
                    data = b'#!/bin/sh\nif then\n'
                member = tarfile.TarInfo('unifi-btv-main/' + relative)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def run_bootstrap(self, shell='bash'):
        result = subprocess.run([shell, '-s', '--', '--download-only', str(self.destination)],
                                input=(BASE / 'install.sh').read_text(), text=True, capture_output=True,
                                cwd=self.root, env=self.environment, timeout=15)
        self.assertFalse(self.marker.exists(), result.stderr)
        self.assertEqual(list(self.scratch.iterdir()), [], result.stderr)
        return result

    def test_piped_download_preserves_complete_payload(self):
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative in PAYLOAD:
            self.assertEqual((self.destination / relative).read_bytes(), (BASE / relative).read_bytes())

    def test_posix_shell_download(self):
        result = self.run_bootstrap('sh')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_download_cleans_temporary_directory(self):
        self.environment['KEEPER_TEST_CURL_FAIL'] = '1'
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_invalid_archive_does_not_install(self):
        self.archive.write_bytes(b'not an archive')
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_missing_required_file_does_not_install(self):
        self.build_archive(omit='src/unifi-btv.py')
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing or empty source file', result.stderr)
        self.assertFalse(self.destination.exists())

    def test_invalid_hook_does_not_install(self):
        self.build_archive(invalid_hook=True)
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_existing_destination_is_not_overwritten(self):
        self.destination.mkdir()
        existing = self.destination / 'keep.txt'
        existing.write_text('keep')
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(existing.read_text(), 'keep')


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(BASE / 'src/unifi-btv.py'))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_default_paths_migrate_and_original_is_retained(self):
        original = self.root / 'old.ini'
        destination = self.root / 'new.ini'
        text = ('[general]\nlog_path = /data/iptv-igmp-keeper/keeper.log\n'
                'state_path = /run/iptv-igmp-keeper/status.json\n'
                '[interfaces]\nupstream = eth4\ndownstream = br935\n'
                '[igmp]\nforce_version = v2\n')
        original.write_text(text)
        self.module['migrate_legacy_config'](original, destination)
        cfg = self.module['read_config'](destination)
        self.assertEqual(cfg['general']['log_path'], '/data/unifi-btv/unifi-btv.log')
        self.assertEqual(cfg['general']['state_path'], '/run/unifi-btv/status.json')
        self.assertEqual(cfg['interfaces']['upstream'], 'eth4')
        self.assertEqual(cfg['igmp']['force_version'], 'v2')
        self.assertEqual(original.read_text(), text)

    def test_custom_paths_are_preserved(self):
        original = self.root / 'old.ini'
        destination = self.root / 'new.ini'
        original.write_text('[general]\nlog_path = /data/my-logs/tv.log\nstate_path = /run/my-tv/status.json\n')
        self.module['migrate_legacy_config'](original, destination)
        cfg = self.module['read_config'](destination)
        self.assertEqual(cfg['general']['log_path'], '/data/my-logs/tv.log')
        self.assertEqual(cfg['general']['state_path'], '/run/my-tv/status.json')


@unittest.skipUnless(shutil.which('sh') and shutil.which('install'), 'POSIX shell and install required')
class InstallMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.destination = self.root / 'unifi-btv'
        self.legacy = self.root / 'legacy'
        self.boot = self.root / 'on_boot.d'
        self.commands = self.root / 'commands'
        self.trace = self.root / 'service-operations.txt'
        for directory in (self.source, self.legacy, self.boot, self.commands):
            directory.mkdir()
        for relative in PAYLOAD:
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = (BASE / relative).read_text()
            if relative == 'install.sh':
                # Redirect only filesystem roots/interpreter; run the full installer.
                content = content.replace('BASE=/data/unifi-btv', 'BASE=' + shlex.quote(str(self.destination)))
                content = content.replace('LEGACY_BASE=/data/iptv-igmp-keeper', 'LEGACY_BASE=' + shlex.quote(str(self.legacy)))
                content = content.replace('/data/on_boot.d', str(self.boot))
                content = content.replace('/run/iptv-igmp-keeper/status.json', str(self.root / 'legacy-run/status.json'))
                content = content.replace('/usr/bin/python3', shlex.quote(sys.executable))
            elif relative == 'unifi/50-unifi-btv.sh':
                content = content.replace('BASE=/data/unifi-btv', 'BASE=' + shlex.quote(str(self.destination)))
                content = content.replace('/usr/bin/python3', shlex.quote(sys.executable))
            target.write_text(content)
        self.make_command('id', '#!/bin/sh\necho 0\n')
        self.make_command('systemctl', '#!/bin/sh\nprintf "%s\n" "$*" >> "$BTV_TEST_TRACE"\ncase "$1" in\nstop) rm -f "$BTV_TEST_ACTIVE" ;;\nis-active) [ -f "$BTV_TEST_ACTIVE" ]; exit $? ;;\nesac\nexit 0\n')
        self.make_command('systemd-run', '#!/bin/sh\nprintf "%s\n" "$*" >> "$BTV_TEST_TRACE"\ntouch "$BTV_TEST_ACTIVE"\nexit 0\n')
        self.environment = dict(os.environ, PATH=str(self.commands) + os.pathsep + os.environ['PATH'],
                                BTV_TEST_TRACE=str(self.trace), BTV_TEST_ACTIVE=str(self.root / 'active'))

    def make_command(self, name, text):
        path = self.commands / name
        path.write_text(text)
        path.chmod(0o755)

    def install(self):
        return subprocess.run(['sh', str(self.source / 'install.sh')], text=True, capture_output=True,
                              env=self.environment, cwd=self.root, timeout=20)

    def prepare_legacy(self):
        text = ('[interfaces]\nupstream = eth4\ndownstream = br935\n'
                '[general]\nlog_path = /data/iptv-igmp-keeper/keeper.log\n'
                'state_path = ' + str(self.root / 'legacy-run/status.json') + '\n')
        (self.legacy / 'config.ini').write_text(text)
        (self.legacy / 'keeper.py').write_text('# obsolete launcher\n')
        (self.legacy / 'uninstall.sh').write_text('# obsolete uninstaller\n')
        (self.legacy / 'keeper.log').write_text('retained log\n')
        (self.boot / '50-iptv-igmp-keeper.sh').write_text('# obsolete boot hook\n')
        return text

    def test_existing_install_migrates_settings_logs_and_boot_hook(self):
        original = self.prepare_legacy()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        module = runpy.run_path(str(BASE / 'src/unifi-btv.py'))
        cfg = module['read_config'](self.destination / 'config.ini')
        self.assertEqual(cfg['interfaces']['upstream'], 'eth4')
        self.assertEqual(cfg['general']['log_path'], '/data/unifi-btv/unifi-btv.log')
        self.assertEqual((self.destination / 'config.legacy.ini').read_text(), original)
        self.assertEqual((self.destination / 'unifi-btv.log').read_text(), 'retained log\n')
        self.assertEqual((self.legacy / 'config.ini').read_text(), original)
        self.assertTrue((self.destination / 'unifi-btv.py').exists())
        self.assertTrue((self.boot / '50-unifi-btv.sh').exists())
        self.assertFalse((self.boot / '50-iptv-igmp-keeper.sh').exists())
        self.assertFalse((self.legacy / 'keeper.py').exists())
        trace = self.trace.read_text()
        self.assertIn('stop iptv-igmp-keeper.service', trace)
        self.assertIn('stop unifi-btv.service', trace)
        self.assertIn('--unit=unifi-btv.service', trace)

    def test_invalid_legacy_config_aborts_before_stopping_or_removing(self):
        self.prepare_legacy()
        (self.legacy / 'config.ini').write_text('[igmp]\nforce_version = invalid\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('stop ', self.trace.read_text())
        self.assertTrue((self.boot / '50-iptv-igmp-keeper.sh').exists())
        self.assertTrue((self.legacy / 'keeper.py').exists())
        self.assertFalse(self.destination.exists())

    def test_existing_new_config_wins_and_repeat_install_preserves_it(self):
        self.prepare_legacy()
        self.destination.mkdir()
        text = '[interfaces]\nupstream = eth9\ndownstream = br99\n'
        (self.destination / 'config.ini').write_text(text)
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.destination / 'config.ini').read_text(), text)
        self.assertFalse((self.destination / 'config.legacy.ini').exists())


if __name__ == '__main__':
    unittest.main()
