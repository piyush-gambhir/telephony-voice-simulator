"""Account number inventory: dry run, backup and restore against a fake manager."""

import json
from pathlib import Path

import pytest

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.service import ValidationError, inventory
from telephony_voice_simulator.telephony.twilio.management import TwilioManagementError

RECORD = {
    "sid": "PN1",
    "phone_number": "+14255550100",
    "friendly_name": "old line",
    "voice_url": "https://old.example/voice",
    "voice_method": "GET",
    "status_callback": "",
    "status_callback_method": "POST",
}


class FakeManager:
    def __init__(self) -> None:
        self.record = dict(RECORD)
        self.updates: list[tuple[str, dict]] = []

    async def list_numbers(self):
        return [self.record]

    async def find_number(self, phone_number):
        if phone_number != self.record["phone_number"]:
            raise TwilioManagementError(f"Expected one Twilio number matching {phone_number}, found 0.")
        return self.record

    async def update_number(self, sid, **changes):
        self.updates.append((sid, changes))
        return {}


async def test_list_returns_voice_urls() -> None:
    rows = await inventory.list_account_numbers(manager=FakeManager())
    assert rows == [
        {
            "sid": "PN1",
            "phone_number": "+14255550100",
            "friendly_name": "old line",
            "voice_url": "https://old.example/voice",
            "voice_method": "GET",
            "status_callback": "",
        }
    ]


async def test_point_dry_run_changes_and_writes_nothing(tmp_path: Path) -> None:
    manager = FakeManager()
    backup = tmp_path / "b.json"
    result = await inventory.point_number(
        number="+14255550100", route="amd", base_url="https://sim.example/",
        backup=backup, dry_run=True, manager=manager,
    )
    assert result["applied"] is False
    assert result["change"]["VoiceUrl"] == "https://sim.example/twilio/voice"
    assert result["backup"]["voice_url"] == "https://old.example/voice"
    assert not backup.exists() and manager.updates == []


async def test_point_saves_backup_then_restore_puts_it_back(tmp_path: Path) -> None:
    manager = FakeManager()
    backup = tmp_path / "nested" / "b.json"
    result = await inventory.point_number(
        number="+14255550100", voice_url="https://x.twil.io/machine?mode=voice",
        status_callback_url="https://sim.example/status", backup=backup, manager=manager,
    )
    assert result["applied"] is True
    saved = json.loads(backup.read_text())
    assert saved["sid"] == "PN1" and saved["voice_method"] == "GET"
    assert manager.updates[0][1]["voice_url"] == "https://x.twil.io/machine?mode=voice"
    assert manager.updates[0][1]["status_callback"] == "https://sim.example/status"

    restored = await inventory.restore_number(
        number="+14255550100", backup=backup, manager=manager
    )
    assert restored["applied"] is True
    assert manager.updates[1][1] == {
        "friendly_name": "old line",
        "voice_url": "https://old.example/voice",
        "voice_method": "GET",
        "status_callback": "",
        "status_callback_method": "POST",
    }


async def test_point_requires_https_url() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        await inventory.point_number(
            number="+14255550100", base_url="http://sim.example", manager=FakeManager()
        )


async def test_restore_refuses_another_numbers_backup(tmp_path: Path) -> None:
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({**RECORD, "sid": "PN2"}))
    manager = FakeManager()
    with pytest.raises(ValidationError, match="does not belong"):
        await inventory.restore_number(number="+14255550100", backup=backup, manager=manager)
    assert manager.updates == []


def test_cli_point_dry_run(monkeypatch, capsys) -> None:
    monkeypatch.setattr(inventory, "TwilioNumberManager", FakeManager)
    code = main([
        "numbers", "point", "--number", "+14255550100", "--route", "ivr",
        "--base-url", "https://sim.example", "--dry-run",
    ])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["change"]["VoiceUrl"] == "https://sim.example/twilio/ivr"
    assert out["applied"] is False


async def test_pointing_twice_keeps_the_first_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(inventory, "RESULTS_DIR", tmp_path)
    manager = FakeManager()
    first = await inventory.point_number(
        number="+14255550100", base_url="https://sim.example/", manager=manager)
    second = await inventory.point_number(
        number="+14255550100", base_url="https://new.example/", manager=manager)
    assert first["backup_path"] != second["backup_path"]
    assert json.loads(Path(first["backup_path"]).read_text())["voice_url"] == (
        "https://old.example/voice")
    with pytest.raises(ValidationError, match="already exists"):
        await inventory.point_number(number="+14255550100", base_url="https://sim.example/",
                                     backup=Path(first["backup_path"]), manager=manager)
