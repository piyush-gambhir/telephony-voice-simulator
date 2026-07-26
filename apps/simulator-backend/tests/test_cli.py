from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest

from telephony_voice_simulator.control.cli import (
    _validate_pstn_serve_configuration,
    main,
)


def test_zero_setup_cli_runs_every_ivr_model_without_creating_a_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    database = tmp_path / "should-not-exist.db"
    monkeypatch.setenv("SIMULATOR_DB_PATH", str(database))
    monkeypatch.setattr("sys.argv", ["telephony-voice-sim", "simulate"])

    assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert len(output) == 7
    assert {item["scenario_id"] for item in output} >= {
        "connect-1501",
        "retry-after-silence",
    }
    assert not database.exists()


def test_zero_setup_cli_accepts_named_pbx_scenario_alias_and_runtime_override(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "telephony-voice-sim",
            "sim",
            "pbx_sales_queue_answers",
            "--pbx",
            "--set",
            "sales-a=busy",
            "--set",
            "sales-b=offline",
        ],
    )

    assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["scenario_id"] == "sales-mainline-open"
    assert output["outcome"] == "overflow"
    assert output["overrides"] == {"sales-a": "busy", "sales-b": "offline"}
    assert any(step["kind"] == "cdr" for step in output["timeline"])


@pytest.mark.parametrize(
    "public_url",
    ("", "not-a-url", "http://simulator.example.test", "ftp://simulator.example.test"),
)
def test_pstn_serve_rejects_missing_or_non_https_public_url(
    public_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", public_url)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", raising=False)

    with pytest.raises(SystemExit, match="absolute HTTPS URL"):
        _validate_pstn_serve_configuration("0.0.0.0")


def test_pstn_serve_accepts_https_with_signature_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://simulator.example.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", raising=False)

    _validate_pstn_serve_configuration("0.0.0.0")


def test_unsigned_webhooks_are_limited_to_loopback_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8978")
    monkeypatch.setenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "true")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    _validate_pstn_serve_configuration("127.0.0.1")
    with pytest.raises(SystemExit, match="absolute HTTPS URL"):
        _validate_pstn_serve_configuration("0.0.0.0")


def test_public_amd_mode_requires_acknowledgement_and_release_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://simulator.example.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", raising=False)
    monkeypatch.delenv("PSTN_ACKNOWLEDGE_PUBLIC_AUDIO", raising=False)

    with pytest.raises(SystemExit, match="PSTN_ACKNOWLEDGE_PUBLIC_AUDIO"):
        _validate_pstn_serve_configuration("0.0.0.0", include_amd=True)

    monkeypatch.setenv("PSTN_ACKNOWLEDGE_PUBLIC_AUDIO", "true")
    with pytest.raises(SystemExit, match="not approved for public exposure"):
        _validate_pstn_serve_configuration("0.0.0.0", include_amd=True)

    # IVR-only exposes no /assets route and therefore needs no audio approval.
    _validate_pstn_serve_configuration("0.0.0.0", include_amd=False)


def test_legacy_console_scripts_point_to_unified_cli() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]

    assert scripts["telephony-voice-sim"] == "telephony_voice_simulator.control.cli:main"
    assert scripts["amd-sim"] == scripts["telephony-voice-sim"]
    assert scripts["telephony-sim"] == scripts["telephony-voice-sim"]


def test_standalone_pstn_entrypoint_uses_public_audio_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telephony_voice_simulator.pstn import server

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://simulator.example.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("PSTN_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("PSTN_ACKNOWLEDGE_PUBLIC_AUDIO", raising=False)

    with pytest.raises(SystemExit, match="PSTN_ACKNOWLEDGE_PUBLIC_AUDIO"):
        server.main()
