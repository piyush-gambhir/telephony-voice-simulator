"""Release checks cover private scenario text as well as audio binaries."""

import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('kind', ['zip', 'tar.gz'])
@pytest.mark.parametrize('member', ['scenarios/test.local.yaml', '.env', 'data/calls.sqlite3',
                                    'private/personas.json'])
def test_release_gate_rejects_private_archive_members(tmp_path, kind, member):
    release = load_script('check_public_release')
    archive = tmp_path / f'package.{kind}'
    if kind == 'zip':
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr(member, 'synthetic test data')
    else:
        with tarfile.open(archive, 'w:gz') as output:
            content = b'synthetic test data'
            entry = tarfile.TarInfo(member)
            entry.size = len(content)
            output.addfile(entry, io.BytesIO(content))
    with pytest.raises(ValueError, match='private configuration or data'):
        release._validate_archive(archive, frozenset())


def test_release_gate_accepts_public_scenario_and_reviewed_audio(tmp_path):
    release = load_script('check_public_release')
    archive = tmp_path / 'public.whl'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('scenarios/test.yaml', 'name: test')
        output.writestr('assets/approved.wav', b'audio')
    release._validate_archive(archive, frozenset({'approved.wav'}))
    with pytest.raises(ValueError, match='non-allowlisted audio'):
        release._validate_archive(archive, frozenset())


def test_static_reference_excludes_local_scenarios_unless_opted_in(tmp_path, monkeypatch):
    generator = load_script('generate_content')
    scenarios = tmp_path / 'scenarios'
    scenarios.mkdir()
    for filename, name in [('public.yaml', 'public_test'), ('private.local.yaml', 'private_test')]:
        (scenarios / filename).write_text(
            f'name: {name}\nmachine:\n  main:\n    - hangup: true\nexpect: {{}}\n'
        )
    monkeypatch.setattr(generator, 'SCENARIOS', scenarios)
    for attr in ['PUBLIC_AUDIO', 'PUBLIC_LICENSE', 'PUBLIC_NOTICE', 'PUBLIC_NOTICES']:
        monkeypatch.setattr(generator, attr, tmp_path / attr)
    monkeypatch.delenv('SIMULATOR_INCLUDE_LOCAL_SCENARIOS', raising=False)
    assert 'private_test' not in generator.build()['scenarios']
    monkeypatch.setenv('SIMULATOR_INCLUDE_LOCAL_SCENARIOS', 'true')
    content = generator.build()
    assert {'private_test', 'public_test'} <= content['scenarios'].keys()

    release = load_script('check_public_release')
    monkeypatch.setattr(release, 'REPO', tmp_path)
    output = tmp_path / 'apps/web-console/content/scenarios.json'
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(content))
    with pytest.raises(ValueError, match='private local scenarios'):
        release._validate_web_output(frozenset())
