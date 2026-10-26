"""Incoming calls and their recordings: listing, media paths and deletion."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ValidationError


class CallsMixin:
    def list_calls(self) -> list[dict[str, Any]]:
        return [call.as_dict() for call in self.store.list_incoming_calls()]

    def _owned_call_directory(self, call_id: str) -> Path | None:
        """Resolve the direct PSTN result directory for a non-traversing call ID."""

        if not call_id or Path(call_id).name != call_id or call_id in {".", ".."}:
            return None
        candidate = self.recordings_root / call_id
        try:
            if candidate.is_symlink():
                return None
            resolved = candidate.resolve()
        except OSError:
            return None
        return resolved if resolved.parent == self.recordings_root else None

    def _owned_recording_path(self, call_id: str, local_path: str) -> Path | None:
        call_directory = self._owned_call_directory(call_id)
        if call_directory is None:
            return None
        try:
            resolved = Path(local_path).resolve()
        except OSError:
            return None
        if resolved == call_directory or not resolved.is_relative_to(call_directory):
            return None
        return resolved

    def delete_call(self, call_id: str) -> dict[str, Any]:
        """Erase persisted call metadata and simulator-owned recording files.

        Only the direct, non-symlinked result directory for this call can be
        removed. External files are deliberately left untouched even if a
        database row happens to reference them.
        """

        call = self.store.get_incoming_call(call_id)
        already_deleted = call is None and self.store.is_incoming_call_deleted(call_id)
        if call is None and not already_deleted:
            raise NotFoundError(f"Unknown incoming call: {call_id}")

        owned_recording_files: set[Path] = set()
        missing_files = 0
        skipped_unowned_files = 0
        external_provider_recordings = []
        if call is not None:
            external_provider_recordings = [
                recording
                for recording in call.recordings
                if recording.provider not in {"", "local", "mock"}
            ]
            for recording in call.recordings:
                if not recording.local_path:
                    continue
                resolved = self._owned_recording_path(call.id, recording.local_path)
                if resolved is None:
                    skipped_unowned_files += 1
                    continue
                if not resolved.is_file():
                    missing_files += 1
                    continue
                owned_recording_files.add(resolved)

            # Persist the tombstone and remove call/recording rows before
            # touching files. Late callbacks in this or another process now
            # fail closed, and a cleanup failure can be retried idempotently.
            if not self.store.delete_incoming_call(call_id):
                raise NotFoundError(f"Unknown incoming call: {call_id}")

        call_directory = self._owned_call_directory(call_id)
        artifacts_directory_deleted = False
        if call_directory is not None and call_directory.is_dir():
            try:
                shutil.rmtree(call_directory)
            except OSError as exc:
                raise ValidationError(
                    "Could not remove the simulator-owned call artifacts; "
                    "metadata is tombstoned and artifact cleanup can be retried"
                ) from exc
            artifacts_directory_deleted = True

        return {
            "deleted": call_id,
            "already_deleted": already_deleted,
            "recording_files": {
                "deleted": len(owned_recording_files),
                "missing": missing_files,
                "skipped_unowned": skipped_unowned_files,
            },
            "external_provider_recordings": {
                "retained": len(external_provider_recordings),
                "providers": sorted(
                    {recording.provider for recording in external_provider_recordings}
                ),
                "note": (
                    "This deletion removes simulator metadata and local artifacts only. "
                    "Provider-hosted recordings follow provider retention and must be "
                    "deleted through the provider separately."
                ),
            },
            "artifacts_directory_deleted": artifacts_directory_deleted,
        }

    def recording_path(self, recording_id: str) -> Path:
        recording = self.store.get_recording(recording_id)
        if recording is None or not recording.local_path:
            raise NotFoundError(f"Recording is not available locally: {recording_id}")
        path = self._owned_recording_path(recording.call_id, recording.local_path)
        if path is None or not path.is_file():
            raise NotFoundError(f"Recording file is missing: {recording_id}")
        return path
