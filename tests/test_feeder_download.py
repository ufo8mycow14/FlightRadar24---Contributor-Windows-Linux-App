"""Optional feeder setup checks in disposable folders; never operate a radio."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows optional-component setup')
class FeederDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='frw-sharing-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'FlightRadar24.exe').write_bytes(b'Unexecuted test marker')
        (self.root/'runtime/feeder').mkdir(parents=True)
        self.destination = self.root/'runtime/feeder/fr24feed'

    def setup_component(self, *extra):
        script = Path(__file__).resolve().parents[1]/'installer/Install-Feeder.ps1'
        return subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-File', str(script),
             '-InstallDirectory', str(self.root), *map(str, extra)],
            capture_output=True, text=True, timeout=90, creationflags=0x08000000)

    def test_rejects_corrupt_download_without_installing(self):
        archive = self.root/'damaged.tgz'
        archive.write_bytes(b'not the official archive')
        result = self.setup_component('-ArchivePath', archive)
        self.assertEqual(result.returncode, 21, result.stdout+result.stderr)
        self.assertIn('did not match', result.stdout)
        self.assertFalse(self.destination.exists())

    def test_preserves_existing_unrecognised_component(self):
        existing = b'A different existing component'
        self.destination.write_bytes(existing)
        result = self.setup_component()
        self.assertEqual(result.returncode, 21, result.stdout+result.stderr)
        self.assertIn('left unchanged', result.stdout)
        self.assertEqual(self.destination.read_bytes(), existing)

    def test_rejects_non_application_folder(self):
        (self.root/'FlightRadar24.exe').unlink()
        result = self.setup_component()
        self.assertEqual(result.returncode, 21, result.stdout+result.stderr)
        self.assertFalse(self.destination.exists())

    def test_provider_prefixed_paths_preserve_component_and_write_summary(self):
        existing = b'A different existing component'
        self.destination.write_bytes(existing)
        summary = self.root/'summary.txt'
        script = Path(__file__).resolve().parents[1]/'installer/Install-Feeder.ps1'
        provider_root = 'Microsoft.PowerShell.Core\\FileSystem::' + str(self.root)
        provider_summary = 'Microsoft.PowerShell.Core\\FileSystem::' + str(summary)
        result = subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-File', str(script),
             '-InstallDirectory', provider_root, '-SummaryPath', provider_summary],
            capture_output=True, text=True, timeout=90, creationflags=0x08000000)
        self.assertEqual(result.returncode, 21, result.stdout+result.stderr)
        self.assertIn('left unchanged', result.stdout)
        self.assertIn('left unchanged', summary.read_text(encoding='utf-8'))
        self.assertEqual(self.destination.read_bytes(), existing)

    @unittest.skipUnless(os.environ.get('FRW_TEST_VENDOR_DOWNLOAD') == '1',
                         'Set FRW_TEST_VENDOR_DOWNLOAD=1 for the official HTTPS download integration check')
    def test_official_download_and_idempotent_repeat(self):
        result = self.setup_component()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(hashlib.sha256(self.destination.read_bytes()).hexdigest(),
                         'a6ea25ce76c4c4341f08a4edf81509314ad414cf6601c10b439f4c7cbbfeaa74')
        self.assertTrue((self.root/'runtime/licences/fr24feed/LICENSE.fr24feed.txt').is_file())
        previous = self.destination.stat().st_mtime_ns
        repeated = self.setup_component()
        self.assertEqual(repeated.returncode, 0, repeated.stdout+repeated.stderr)
        self.assertIn('already installed', repeated.stdout)
        self.assertEqual(self.destination.stat().st_mtime_ns, previous)


if __name__ == '__main__':
    unittest.main()
