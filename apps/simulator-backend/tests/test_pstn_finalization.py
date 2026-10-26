"""Controlled callback/media races without external calls or transcription."""

import asyncio
import io
from threading import Event
from urllib.parse import urlsplit
from xml.etree import ElementTree

import numpy as np
import pytest
import soundfile as sf
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.storage.sqlite import SimulatorStore
from telephony_voice_simulator.telephony.twilio import callbacks as twilio_callbacks
from telephony_voice_simulator.telephony.twilio import recordings as twilio_recordings
from telephony_voice_simulator.telephony.twilio import routing as twilio_routing
from telephony_voice_simulator.telephony.twilio import security as twilio_security
from telephony_voice_simulator.telephony.twilio import state as twilio_state
from telephony_voice_simulator.telephony.twilio import webhooks as twilio_webhooks
from telephony_voice_simulator.telephony.twilio.compile import compile_scenario

SID = 'CA-finalization'
RECORDING = 'RE' + '1' * 32


@pytest.fixture
async def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv('TWILIO_AUTH_TOKEN', raising=False)
    monkeypatch.setenv('PSTN_ALLOW_UNSIGNED_WEBHOOKS', 'true')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://sim.example.test')
    monkeypatch.setattr(twilio_state, 'PSTN_RESULTS_DIR', tmp_path / 'results')
    monkeypatch.setattr(twilio_state, 'STATE', twilio_state.SimState())
    monkeypatch.setattr(twilio_state, 'CALL_STORE', SimulatorStore(tmp_path / 'calls.sqlite3'))
    scenario = {'name': 'test_media', 'machine': {'main': [{'hold': 10}]},
                'expect': {'agent_spoke': True}}
    compiled = compile_scenario(scenario, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    twilio_state.STATE.scenarios[compiled.name] = compiled
    twilio_state.STATE.raw_scenarios[compiled.name] = scenario
    twilio_state.STATE.default_scenario = compiled.name
    async with TestClient(TestServer(twilio_webhooks.build_app())) as client:
        yield client, twilio_state.CALL_STORE, tmp_path


async def answer(client):
    response = await client.post('/twilio/voice', data={'CallSid': SID, 'To': '+15555550100'})
    assert response.status == 200
    assert '<Record' in await response.text()


async def complete(client, status='completed'):
    response = await client.post('/twilio/status', data={
        'CallSid': SID, 'CallStatus': status, 'CallDuration': '8',
    })
    assert response.status == 200


async def recording(client, status='completed'):
    response = await client.post('/twilio/recording', data={
        'CallSid': SID, 'RecordingSid': RECORDING, 'RecordingStatus': status,
        'RecordingSource': 'RecordVerb',
    })
    assert response.status == 200


async def settled(store):
    if task := twilio_state.STATE.finalization_tasks.get(SID):
        await asyncio.wait_for(task, 3)
    saved = store.get_incoming_call(SID)
    assert saved.analysis['finalization']['state'] == 'complete'
    return saved


def speech_wav():
    output = io.BytesIO()
    samples = (8000 * np.sin(2 * np.pi * 300 * np.arange(8000) / 8000)).astype(np.int16)
    sf.write(output, samples, 8000, format='WAV')
    return output.getvalue()


@pytest.fixture
async def media(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def download(request):
        requests.append(request.path)
        audio = speech_wav()
        response = web.StreamResponse(headers={'Content-Type': 'audio/wav'})
        await response.prepare(request)
        await response.write(audio[:44])
        entered.set()
        await release.wait()
        try:
            await response.write(audio[44:])
            await response.write_eof()
        except ConnectionResetError:
            pass  # Shutdown/deletion intentionally cancels the reader.
        return response

    app = web.Application()
    app.router.add_get('/recording.wav', download)
    async with TestServer(app) as server:
        for module in (twilio_callbacks, twilio_recordings):
            monkeypatch.setattr(module, 'recording_media_url',
                                lambda sid: str(server.make_url('/recording')))
        yield entered, release, requests
        release.set()


async def test_finalization_waits_for_media_and_deduplicates_callbacks(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    entered, release, requests = media
    await answer(client)
    await recording(client)
    await asyncio.wait_for(entered.wait(), 2)
    await recording(client)
    await complete(client)
    job = twilio_state.STATE.finalization_tasks[SID]
    await complete(client)
    await asyncio.sleep(0)
    assert twilio_state.STATE.finalization_tasks[SID] is job
    assert not job.done()
    assert store.get_incoming_call(SID).analysis['finalization']['state'] == 'pending'
    # Restart/reload must not silently grade against a changed scenario definition.
    twilio_state.STATE.raw_scenarios['test_media']['expect']['agent_spoke'] = False
    release.set()
    await asyncio.wait_for(job, 3)
    saved = store.get_incoming_call(SID)
    assert saved.analysis['passed'] is True
    assert saved.duration_s == 8
    assert len(requests) == 1
    assert len(saved.recordings) == 1
    assert saved.analysis['finalization']['state'] == 'complete'
    analysis = saved.analysis
    await recording(client)
    await recording(client, 'in-progress')
    await complete(client)
    assert not twilio_state.STATE.finalization_tasks
    assert store.get_incoming_call(SID).analysis == analysis
    assert store.get_recording(RECORDING).status == 'completed'
    assert len(requests) == 1


async def test_missing_media_is_indeterminate_and_late_media_updates_linked_run(
    workspace, media, monkeypatch,
):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    await answer(client)
    connection = store.create_connection('mock', 'Test')
    endpoint = store.create_endpoint(connection_id=connection.id, name='Test', kind='extension',
                                     address='1001', default_scenario='test_media')
    run = store.create_run(endpoint.id, 'twilio', 'test_media')
    twilio_state.STATE.calls[SID]['run_id'] = run.id
    twilio_routing.update_simulation_run(run.id, status='in-progress', call_id=SID)
    await complete(client)
    await settled(store)
    first = store.get_run(run.id)
    assert first.result['passed'] is None
    assert first.result['graded'] is False
    assert store.get_incoming_call(SID).analysis['passed'] is None
    _, release, _ = media
    release.set()
    await recording(client)
    await settled(store)
    updated = store.get_run(run.id)
    assert updated.result['passed'] is True
    assert updated.result['graded'] is True
    assert updated.completed_at == first.completed_at
    assert len(updated.timeline) == len(first.timeline)


async def test_shutdown_leaves_recoverable_job_and_restart_finishes(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 60)
    client, store, _ = workspace
    entered, release, _ = media
    await answer(client)
    await recording(client)
    await asyncio.wait_for(entered.wait(), 2)
    await complete(client)
    await client.close()
    assert store.get_incoming_call(SID).analysis['finalization']['state'] == 'pending'
    assert not twilio_state.STATE.finalization_tasks
    assert not twilio_state.STATE.recording_tasks
    twilio_state.STATE = twilio_state.SimState()
    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    release.set()
    async with TestClient(TestServer(twilio_webhooks.build_app())):
        await settled(store)
        assert store.get_incoming_call(SID).analysis['passed'] is True


async def test_full_call_download_after_grading_survives_restart(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    entered, release, _ = media
    await answer(client)
    await complete(client)
    await settled(store)
    response = await client.post('/twilio/recording', data={
        'CallSid': SID, 'RecordingSid': RECORDING, 'RecordingStatus': 'completed',
        'RecordingSource': 'StartCallRecordingAPI',
    })
    assert response.status == 200
    await asyncio.wait_for(entered.wait(), 2)
    assert not twilio_state.STATE.finalization_tasks
    await client.close()
    assert store.get_incoming_call(SID).analysis['finalization']['state'] == 'complete'
    assert store.get_recording(RECORDING).local_path is None
    twilio_state.STATE = twilio_state.SimState()
    release.set()
    async with TestClient(TestServer(twilio_webhooks.build_app())):
        tasks = list(twilio_state.STATE.recording_tasks.get(SID, {}).values())
        await asyncio.wait_for(asyncio.gather(*tasks), 3)
        assert store.get_recording(RECORDING).local_path
        assert store.get_incoming_call(SID).analysis['passed'] is None


async def test_deletion_during_download_cannot_restore_files_or_metadata(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, root = workspace
    entered, release, _ = media
    await answer(client)
    await recording(client)
    await asyncio.wait_for(entered.wait(), 2)
    await complete(client)
    job = twilio_state.STATE.finalization_tasks[SID]
    store.delete_incoming_call(SID)
    twilio_state.evict_call(SID)
    release.set()
    await asyncio.gather(job, return_exceptions=True)
    assert store.get_incoming_call(SID) is None
    assert not (root / 'results' / SID).exists()


async def test_stale_nonterminal_status_cannot_reopen_runtime(workspace, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    await answer(client)
    await complete(client)
    await settled(store)
    ended_at = store.get_incoming_call(SID).ended_at
    await complete(client, 'in-progress')
    assert twilio_state.STATE.calls[SID]['status'] == 'completed'
    assert store.get_incoming_call(SID).ended_at == ended_at
    response = await client.post('/twilio/voice', data={'CallSid': SID, 'To': '+15555550100'})
    assert '<Hangup' in await response.text()


async def test_explicit_absent_media_is_an_observed_failure(workspace, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    await answer(client)
    await recording(client, 'absent')
    await complete(client)
    saved = await settled(store)
    assert saved.analysis['passed'] is False
    assert saved.analysis['graded'] is True


async def test_message_callbacks_keep_execution_order_and_reject_rebinding(workspace):
    from telephony_voice_simulator.telephony.twilio import finalization

    client, store, root = workspace
    raw = twilio_state.STATE.raw_scenarios['test_media']
    raw['machine']['main'] = [{'hold': 5}, {'tone': {'duration': 1}}, {'hold': 5}]
    twilio_state.STATE.scenarios['test_media'] = compile_scenario(
        raw, assets_dir=root, out_dir=root / 'compiled',
    )
    response = await client.post('/twilio/voice', data={'CallSid': SID})
    action = ElementTree.fromstring(await response.text()).find('Record').attrib['action']
    action_url = urlsplit(action)
    await client.post(action_url.path + '?' + action_url.query, data={'CallSid': SID})
    first_sid, second_sid = 'RE' + '9' * 32, 'RE' + '1' * 32
    for step, recording_sid in [(1, second_sid), (0, first_sid)]:
        response = await client.post(f'/twilio/recording?step={step}', data={
            'CallSid': SID, 'RecordingSid': recording_sid, 'RecordingStatus': 'absent',
        })
        assert response.status == 200
        path = twilio_security.call_results_dir(SID) / f'{recording_sid}.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(speech_wav())
        store.upsert_recording(recording_id=recording_sid, call_id=SID, provider='twilio',
                               kind='message', status='completed', local_path=str(path))
        if step == 1:
            _, _, incomplete, evidence = finalization.media_state(twilio_state.STATE.calls[SID])
            assert incomplete is True
            assert evidence['message_recording_file'] is None
    _, paths, incomplete, evidence = finalization.media_state(twilio_state.STATE.calls[SID])
    assert [path.split('/')[-1] for path in paths] == [f'{first_sid}.wav', f'{second_sid}.wav']
    assert incomplete is False
    assert evidence['message_recording_order_known'] is True
    assert evidence['message_recording_file'] == paths[0]
    assert evidence['message_recording_window']['step'] == 0
    response = await client.post('/twilio/recording?step=1', data={
        'CallSid': SID, 'RecordingSid': first_sid, 'RecordingStatus': 'completed',
    })
    assert response.status == 400


async def test_full_call_audio_is_not_graded_as_the_mailbox_message(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    _, release, _ = media
    release.set()
    await answer(client)
    response = await client.post('/twilio/recording', data={
        'CallSid': SID, 'RecordingSid': RECORDING, 'RecordingStatus': 'completed',
        'RecordingSource': 'StartCallRecordingAPI',
    })
    assert response.status == 200
    await complete(client)
    saved = await settled(store)
    assert saved.recordings[0].local_path
    assert saved.analysis['passed'] is None
    assert saved.analysis['recordings_analyzed'] == []


async def test_new_media_arriving_during_analysis_replaces_stale_result(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    entered, release_analysis = Event(), Event()
    analyze = finalization.analyze_call
    analyses = []

    def paused(call, *args):
        analyses.append(list(call.get('analysis_recording_files', [])))
        if len(analyses) == 1:
            entered.set()
            assert release_analysis.wait(3)
        return analyze(call, *args)

    monkeypatch.setattr(finalization, 'analyze_call', paused)
    client, store, _ = workspace
    media_entered, release_media, _ = media
    release_media.set()
    await answer(client)
    await complete(client)
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await recording(client)
        await asyncio.wait_for(media_entered.wait(), 2)
    finally:
        release_analysis.set()
    saved = await settled(store)
    assert saved.analysis['passed'] is True
    assert analyses[0] == []
    assert analyses[-1]


@pytest.mark.parametrize('query', ['index=invalid', 'index=-1', 'index=100', 'seq=unknown'])
async def test_invalid_continuation_fails_closed(workspace, query):
    client, _, _ = workspace
    await answer(client)
    response = await client.post('/twilio/step?' + query, data={'CallSid': SID})
    assert response.status == 200
    assert '<Hangup' in await response.text()


async def test_new_message_marks_previous_pass_pending_while_downloading(workspace, media, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    client, store, _ = workspace
    entered, release, _ = media
    release.set()
    await answer(client)
    connection = store.create_connection('mock', 'Test')
    endpoint = store.create_endpoint(connection_id=connection.id, name='Test', kind='extension',
                                     address='1001', default_scenario='test_media')
    run = store.create_run(endpoint.id, 'twilio', 'test_media')
    twilio_state.STATE.calls[SID]['run_id'] = run.id
    twilio_routing.update_simulation_run(run.id, status='in-progress', call_id=SID)
    await recording(client)
    await complete(client)
    await settled(store)
    assert store.get_run(run.id).result['passed'] is True
    release.clear()
    entered.clear()
    response = await client.post('/twilio/recording', data={
        'CallSid': SID, 'RecordingSid': 'RE' + '2' * 32, 'RecordingStatus': 'completed',
        'RecordingSource': 'RecordVerb',
    })
    assert response.status == 200
    await asyncio.wait_for(entered.wait(), 2)
    assert store.get_run(run.id).result['passed'] is None
    assert store.get_run(run.id).result['graded'] is False
    release.set()
    await settled(store)
    assert store.get_run(run.id).result['passed'] is True


async def test_failed_download_cannot_pass_a_silence_expectation(workspace, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    monkeypatch.setattr(twilio_recordings, 'RECORDING_DOWNLOAD_ATTEMPTS', 1)
    client, store, _ = workspace
    twilio_state.STATE.raw_scenarios['test_media']['expect']['agent_spoke'] = False
    async with TestServer(web.Application()) as server:
        for module in (twilio_callbacks, twilio_recordings):
            monkeypatch.setattr(module, 'recording_media_url',
                                lambda sid: str(server.make_url('/missing')))
        await answer(client)
        await recording(client)
        await complete(client)
        saved = await settled(store)
    assert store.get_recording(RECORDING).status == 'download_failed'
    assert saved.analysis['passed'] is None
    assert saved.analysis['graded'] is False


async def test_analysis_does_not_block_webhooks_and_deletion_discards_its_result(
    workspace, monkeypatch,
):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    entered, release = Event(), Event()
    analyze = finalization.analyze_call

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return analyze(*args)

    monkeypatch.setattr(finalization, 'analyze_call', blocked)
    client, store, root = workspace
    await answer(client)
    await complete(client)
    job = twilio_state.STATE.finalization_tasks[SID]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        response = await asyncio.wait_for(client.post('/twilio/status', data={
            'CallSid': 'CA-other', 'CallStatus': 'completed',
        }), 1)
        assert response.status == 200
        store.delete_incoming_call(SID)
        twilio_state.evict_call(SID)
    finally:
        release.set()
    await asyncio.gather(job, return_exceptions=True)
    assert store.get_incoming_call(SID) is None
    assert not (root / 'results' / SID).exists()


async def test_analysis_error_is_persisted_and_recovered_on_restart(workspace, monkeypatch):
    from telephony_voice_simulator.telephony.twilio import finalization

    monkeypatch.setattr(finalization, 'CALLBACK_GRACE_SECONDS', 0)
    analyze = finalization.analyze_call

    def fail(*args):
        raise ValueError('synthetic failure')

    monkeypatch.setattr(finalization, 'analyze_call', fail)
    client, store, _ = workspace
    await answer(client)
    await recording(client, 'absent')
    await complete(client)
    if job := twilio_state.STATE.finalization_tasks.get(SID):
        await asyncio.wait_for(job, 3)
    saved = store.get_incoming_call(SID)
    assert saved.analysis['finalization']['state'] == 'error'
    assert saved.analysis['finalization']['error_type'] == 'ValueError'
    await client.close()
    twilio_state.STATE = twilio_state.SimState()
    monkeypatch.setattr(finalization, 'analyze_call', analyze)
    async with TestClient(TestServer(twilio_webhooks.build_app())):
        saved = await settled(store)
    assert saved.analysis['passed'] is False


def test_pending_job_discovery_is_not_limited_to_history_page(tmp_path):
    store = SimulatorStore(tmp_path / 'recovery.sqlite3')
    store.upsert_incoming_call(call_id=SID, started_at='2020-01-01T00:00:00Z', status='completed',
                               analysis={'finalization': {'state': 'pending'}})
    for index in range(101):
        store.upsert_incoming_call(call_id=f'CA-history-{index}', status='completed')
    assert SID not in {call.id for call in store.list_incoming_calls()}
    assert [call.id for call in store.list_calls_awaiting_finalization()] == [SID]


async def test_missing_later_record_window_keeps_media_incomplete(workspace):
    from telephony_voice_simulator.telephony.twilio import finalization

    client, _, root = workspace
    raw = twilio_state.STATE.raw_scenarios['test_media']
    raw['machine']['main'] = [{'hold': 5}, {'tone': {'duration': 1}}, {'hold': 5}]
    twilio_state.STATE.scenarios['test_media'] = compile_scenario(
        raw, assets_dir=root, out_dir=root / 'compiled',
    )
    response = await client.post('/twilio/voice', data={'CallSid': SID})
    action = urlsplit(ElementTree.fromstring(await response.text()).find('Record').attrib['action'])
    await client.post(action.path + '?' + action.query, data={'CallSid': SID})
    response = await client.post('/twilio/recording?step=0', data={
        'CallSid': SID, 'RecordingSid': RECORDING, 'RecordingStatus': 'absent',
    })
    assert response.status == 200
    _, _, incomplete, _ = finalization.media_state(twilio_state.STATE.calls[SID])
    assert incomplete is True


async def test_late_binding_during_pending_grading_survives_restart(workspace):
    from telephony_voice_simulator.telephony.twilio import finalization

    client, store, _ = workspace
    await answer(client)
    await complete(client)
    assert twilio_state.STATE.finalization_tasks.get(SID) is not None  # still in its callback grace
    response = await client.post('/twilio/recording?step=0', data={
        'CallSid': SID, 'RecordingSid': RECORDING, 'RecordingStatus': 'in-progress',
    })
    assert response.status == 200
    saved = store.get_incoming_call(SID).analysis['finalization']
    assert saved['state'] == 'pending'
    assert saved['runtime']['recording_windows'] == {RECORDING: 0}
    twilio_state.STATE.finalization_tasks.pop(SID).cancel()
    twilio_state.STATE.calls.clear()
    assert finalization.restore(SID)['recording_windows'] == {RECORDING: 0}
