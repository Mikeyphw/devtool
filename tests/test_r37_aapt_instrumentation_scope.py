"""R37-R6: compiled aapt manifest must scope each element's attributes."""
import unittest
from devtool.actions_android import _instrumentation_xml
from devtool.github_actions import GitHubActionsError

APP = "com.mikeyphw.mmrl.debug"
TEST = APP + ".test"
RUNNER = "androidx.test.runner.AndroidJUnitRunner"

XML = f'''
E: manifest (line=2)
  A: package="{TEST}" (Raw: "{TEST}")
  A: android:versionCode(0x0101021b)="" (Raw: "")
  E: uses-sdk (line=3)
    A: android:name(0x01010003)="different.nested.Name" (Raw: "different.nested.Name")
  E: application (line=4)
    A: android:name(0x01010003)="android.app.Application" (Raw: "android.app.Application")
    A: android:label(0x01010001)="MMRL Test"
    E: activity (line=5)
      A: android:name(0x01010003)="another.nested.Activity"
  E: instrumentation (line=6)
    A: android:name(0x01010003)="{RUNNER}" (Raw: "{RUNNER}")
    A: android:targetPackage(0x01010021)="{APP}" (Raw: "{APP}")
    A: android:label(0x01010001)="MMRL Test Suite"
'''

class InstrumentationManifestScopeTests(unittest.TestCase):
    def test_valid_manifest_with_nested_duplicate_attribute_names(self):
        self.assertEqual(_instrumentation_xml(XML, test_package=TEST, target_package=APP), RUNNER)

    def test_direct_duplicate_instrumentation_attribute_rejected(self):
        bad = XML.replace('    A: android:targetPackage',
            '    A: android:name="fake.second.Runner"\n    A: android:targetPackage')
        with self.assertRaisesRegex(GitHubActionsError, 'duplicate'):
            _instrumentation_xml(bad, test_package=TEST, target_package=APP)

    def test_direct_duplicate_manifest_package_rejected(self):
        bad = XML.replace('  A: android:versionCode',
            '  A: package="other.package"\n  A: android:versionCode')
        with self.assertRaisesRegex(GitHubActionsError, 'duplicate'):
            _instrumentation_xml(bad, test_package=TEST, target_package=APP)

    def test_wrong_target_rejected(self):
        bad = XML.replace(f'="{APP}" (Raw: "{APP}")', '="invalid.target"')
        with self.assertRaisesRegex(GitHubActionsError, 'targetPackage'):
            _instrumentation_xml(bad, test_package=TEST, target_package=APP)

    def test_wrong_manifest_package_rejected(self):
        with self.assertRaisesRegex(GitHubActionsError, 'package'):
            _instrumentation_xml(XML, test_package='unrelated.test', target_package=APP)

    def test_missing_instrumentation_rejected(self):
        with self.assertRaisesRegex(GitHubActionsError, 'missing or ambiguous'):
            _instrumentation_xml(XML.replace('E: instrumentation', 'E: receiver'), test_package=TEST, target_package=APP)

    def test_multiple_instrumentation_nodes_rejected(self):
        bad = XML + f'  E: instrumentation\n    A: android:name="{RUNNER}"\n    A: android:targetPackage="{APP}"\n'
        with self.assertRaisesRegex(GitHubActionsError, 'missing or ambiguous'):
            _instrumentation_xml(bad, test_package=TEST, target_package=APP)

    def test_invalid_runner_rejected(self):
        bad = XML.replace(f'="{RUNNER}"', '="non.qualified."')
        with self.assertRaisesRegex(GitHubActionsError, 'runner'):
            _instrumentation_xml(bad, test_package=TEST, target_package=APP)

if __name__ == '__main__':
    unittest.main()
