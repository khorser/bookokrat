"""Exercise Windows packaging/publication with fake tools; no network or Windows required."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


TOOLS = Path(__file__).resolve().parents[1]


class WindowsCiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")

    def tool(self, name, source):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)

    def run_script(self, name, *args):
        return subprocess.run(
            [shutil.which("bash"), str(TOOLS / name), *args],
            cwd=self.root, env=self.env, capture_output=True, text=True,
        )

    def prepare_package(self, missing=False):
        toolchain = self.root / "ucrt64" / "bin"
        system = self.root / "Windows" / "System32"
        toolchain.mkdir(parents=True)
        system.mkdir(parents=True)
        (system / "KERNEL32.dll").touch()
        (self.root / "bookokrat.exe").write_text("exe")
        (toolchain / "one.dll").write_text("one")
        if not missing:
            (toolchain / "two.dll").write_text("two")
        self.env.update(MINGW_PREFIX=str(toolchain.parent), WINDIR=str(system.parent))
        self.tool("cygpath", "import sys\nprint(sys.argv[2])\n")
        self.tool("objdump", """import pathlib, sys
imports = {'bookokrat.exe': ['KERNEL32.dll', 'api-ms-win-core.dll', 'one.dll'],
           'one.dll': ['two.dll'], 'two.dll': ['one.dll']}
for dll in imports[pathlib.Path(sys.argv[2]).name]:
    print('DLL Name:', dll)
""")
        self.tool("7z", """import pathlib, sys, zipfile
with zipfile.ZipFile(sys.argv[2], 'w') as archive:
    for name in sys.argv[3:]:
        archive.write(name, pathlib.Path(name).name)
""")

    def test_bundles_transitive_dlls_and_checksum(self):
        self.prepare_package()
        result = self.run_script("package_windows.sh", "bookokrat.exe", "bundle.zip")
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / "bundle.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"bookokrat.exe", "one.dll", "two.dll"})
        expected = hashlib.sha256((self.root / "bundle.zip").read_bytes()).hexdigest()
        self.assertEqual((self.root / "bundle.zip.sha256").read_text().strip(), f"{expected}  bundle.zip")

    def test_missing_dependency_fails_before_packaging(self):
        self.prepare_package(missing=True)
        result = self.run_script("package_windows.sh", "bookokrat.exe", "bundle.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unresolved runtime DLL: two.dll", result.stderr)
        self.assertFalse((self.root / "bundle.zip").exists())

    def publish(self, state="existing"):
        self.env.update(
            GITHUB_REPOSITORY="owner/repo", GITHUB_SHA="abc123", GITHUB_REF_NAME="windows-support",
            MOCK_STATE=state, MOCK_LOG=str(self.root / "gh.log"),
        )
        self.tool("gh", """import json, os, sys
args = sys.argv[1:]
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
state = os.environ['MOCK_STATE']
if args[:2] == ['api', '--paginate']:
    if args[2].endswith('/releases'):
        if state == 'network-failure':
            sys.exit(1)
        if state != 'new':
            print('123')
    else:
        print('1\\tbookokrat-dev-old-x86_64-pc-windows-gnu.zip')
        print('2\\tbookokrat-dev-old-x86_64-pc-windows-gnu.zip.sha256')
        print('3\\tbookokrat-linux.tar.gz')
        print('4\\tbookokrat-dev-new-x86_64-pc-windows-gnu.zip')
        print('5\\tbookokrat-dev-new-x86_64-pc-windows-gnu.zip.sha256')
elif args[:2] == ['release', 'upload'] and state == 'upload-failure':
    sys.exit(1)
""")
        result = self.run_script("publish_windows_dev.sh", "bookokrat-dev-new-x86_64-pc-windows-gnu.zip")
        calls = [json.loads(line) for line in (self.root / "gh.log").read_text().splitlines()]
        return result, calls

    def test_update_uploads_before_retargeting_and_removes_only_old_windows_assets(self):
        result, calls = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1][:2], ["release", "upload"])
        self.assertEqual(calls[2][:3], ["api", "--method", "PATCH"])
        deletions = [call[3] for call in calls if call[:3] == ["api", "--method", "DELETE"]]
        self.assertEqual(deletions, ["repos/owner/repo/releases/assets/1", "repos/owner/repo/releases/assets/2"])
        self.assertFalse(any(call[:2] == ["release", "delete"] for call in calls))

    def test_first_release_also_retargets_an_orphaned_tag(self):
        result, calls = self.publish("new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1][:2], ["release", "create"])
        self.assertEqual(calls[2][:3], ["api", "--method", "PATCH"])
        self.assertEqual(len(calls), 3)

    def test_failed_upload_preserves_tag_and_old_assets(self):
        result, calls = self.publish("upload-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)

    def test_network_failure_is_not_treated_as_a_missing_release(self):
        result, calls = self.publish("network-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
