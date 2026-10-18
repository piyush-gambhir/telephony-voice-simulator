import base64
import hashlib
import hmac
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.pstn import sms_receiver


@pytest.fixture
async def inbox(tmp_path, monkeypatch):
    path = tmp_path / 'sms.sqlite3'
    for key, value in {
        'PUBLIC_BASE_URL': 'https://sim.example', 'TWILIO_AUTH_TOKEN': 'test-token',
        'TWILIO_ACCOUNT_SID': 'ACtest', 'PSTN_SMS_NUMBERS': '+15555550100',
        'PSTN_SMS_ALLOWED_SENDERS': '+15555550101', 'PSTN_SMS_DB': str(path),
    }.items():
        monkeypatch.setenv(key, value)
    app = web.Application()
    sms_receiver.register_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client, path


async def post(client, **extra):
    data = {'MessageSid': 'SM' + '1' * 32, 'AccountSid': 'ACtest',
            'From': '+15555550101', 'To': '+15555550100', 'Body': 'Recall test', **extra}
    payload = 'https://sim.example/twilio/sms' + ''.join(k + data[k] for k in sorted(data))
    signature = base64.b64encode(hmac.new(b'test-token', payload.encode(), hashlib.sha1).digest()).decode()
    return await client.post('/twilio/sms', data=data, headers={'X-Twilio-Signature': signature})


async def test_signed_delivery_is_durable_and_duplicate_is_not_repeated(inbox):
    client, path = inbox
    for _ in range(2):
        assert (await post(client)).status == 200
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sender,recipient,body FROM sms_messages').fetchall() == [
            ('+15555550101', '+15555550100', 'Recall test')]
    assert path.stat().st_mode & 0o777 == 0o600


async def test_unsigned_sms_never_persists(inbox):
    client, path = inbox
    assert (await client.post('/twilio/sms', data={'Body': 'test'})).status == 403
    assert not path.exists()


@pytest.mark.parametrize('changes,status', [
    ({'From': '+15555550999'}, 403), ({'To': '+15555550999'}, 403),
    ({'AccountSid': 'ACwrong'}, 403), ({'MessageSid': 'bad'}, 400),
    ({'Body': 'x' * 1601}, 400), ({'NumMedia': '1'}, 415),
])
async def test_rejects_other_destinations_accounts_and_unsupported_payloads(inbox, changes, status):
    client, path = inbox
    assert (await post(client, **changes)).status == status
    assert not path.exists()
