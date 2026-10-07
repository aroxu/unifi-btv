"""Exercise the piped bootstrap without installing or modifying gateway state."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
PAYLOAD = ['src/iptv-igmp-keeper.py', 'config/config.example.ini',
           'uninstall.sh', 'unifi/50-iptv-igmp-keeper.sh', 'install.sh']


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
                if invalid_hook and relative == 'unifi/50-iptv-igmp-keeper.sh':
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
        self.build_archive(omit='src/iptv-igmp-keeper.py')
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


if __name__ == '__main__':
    unittest.main()
