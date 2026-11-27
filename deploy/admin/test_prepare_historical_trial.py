"""Linux-only migration scope regression; run with python3 -m unittest discover here."""
import importlib.util
import os
from pathlib import Path
import pwd
import grp
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare_history', Path(__file__).with_name('prepare-historical-trial.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@unittest.skipUnless(os.geteuid() == 0, 'ownership migration requires root on Linux')
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.group = grp.getgrgid(os.getgid()).gr_name

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_only_history_and_preserve_parent(self):
        outside = self.base / 'production'
        outside.write_bytes(b'untouched'); outside.chmod(0o600)
        original = outside.stat()
        root = self.base / 'history'; root.mkdir(mode=0o700)
        legacy = root / 'old.json'; legacy.write_bytes(b'{}'); legacy.chmod(0o600)
        parent = self.base.stat()
        module.prepare(root, self.user, self.group)
        self.assertEqual(root.stat().st_mode & 0o7777, 0o2770)
        self.assertEqual(legacy.stat().st_mode & 0o777, 0o640)
        self.assertEqual(outside.stat().st_mode, original.st_mode)
        self.assertEqual(self.base.stat().st_mode, parent.st_mode)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_reject_links_before_changing_existing_entries(self):
        root = self.base / 'history'; root.mkdir(mode=0o700)
        outside = self.base / 'outside'; outside.write_bytes(b'x'); outside.chmod(0o600)
        (root / 'link').symlink_to(outside)
        with self.assertRaises(ValueError): module.prepare(root, self.user, self.group)
        self.assertEqual(root.stat().st_mode & 0o7777, 0o700)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o600)

    def test_reject_hardlinks(self):
        root = self.base / 'history'; root.mkdir(mode=0o700)
        outside = self.base / 'outside'; outside.write_bytes(b'x')
        os.link(outside, root / 'hardlink')
        with self.assertRaises(ValueError): module.prepare(root, self.user, self.group)
        self.assertEqual(root.stat().st_mode & 0o7777, 0o700)


if __name__ == '__main__':
    unittest.main()
