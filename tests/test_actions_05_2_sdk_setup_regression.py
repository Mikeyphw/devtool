"""ACTIONS-05.2: reject SDK setup's deprecated `tools` request in CI."""
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / '.github/workflows/devtool-android-build.yml',
    ROOT / 'docs/templates/devtool-android-build.yml',
)


def test_workflows_use_current_android_sdk_action_and_explicit_supported_packages():
    for path in FILES:
        steps = yaml.safe_load(path.read_text())['jobs']['build']['steps']
        sdk = [s for s in steps if str(s.get('uses', '')).startswith('android-actions/setup-android@')]
        assert len(sdk) == 1, path
        assert sdk[0]['uses'] == 'android-actions/setup-android@v4', path
        packages = sdk[0]['with']['packages'].split()
        assert packages == ['platform-tools'], path
        assert 'tools' not in packages, path
        assert 'platforms;android-36' not in packages, path


def test_live_hud_and_template_remain_equivalent_at_android_setup_boundary():
    workflow, template = (yaml.safe_load(path.read_text()) for path in FILES)
    for item in (workflow, template):
        steps = item['jobs']['build']['steps']
        assert steps[1]['with']['java-version'] == '21'
        assert steps[2]['uses'] == 'android-actions/setup-android@v4'
        assert steps[2]['with'] == {'packages': 'platform-tools'}
        assert any(s.get('uses') == 'gradle/actions/setup-gradle@v4' for s in steps)
        assert any(s.get('uses') == 'actions/upload-artifact@v4' for s in steps)
    assert workflow['jobs'] == template['jobs']


def test_no_legacy_setup_android_action_or_tools_package_in_workflow_templates():
    for path in FILES:
        source = path.read_text()
        assert not re.search(r'android-actions/setup-android@v[123]\b', source)
        assert not re.search(r'packages:\s*[\"\']?tools(?:\s|[\"\']|$)', source)
