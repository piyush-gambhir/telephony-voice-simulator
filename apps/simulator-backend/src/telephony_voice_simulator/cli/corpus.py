"""Audio corpus: build, import, public-serving validation and IVR prompts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..paths import ASSETS_DIR, CORPUS_MANIFEST, SCENARIOS_DIR
from ..service import corpus
from .common import action, emit, group


def _log(line: str) -> None:
    print(line, file=sys.stderr)


def _build(args: argparse.Namespace) -> int:
    return emit(
        corpus.build_corpus(
            only=args.only, force=args.force, replace_imported=args.replace_imported, log=_log
        )
    )


def _import(args: argparse.Namespace) -> int:
    return emit(
        corpus.import_audio(
            asset=args.asset,
            source=args.source,
            source_type=args.source_type,
            license_note=args.license_note,
            transcript=args.transcript,
            commit_allowed=args.commit_allowed,
        )
    )


def _from_recording(args: argparse.Namespace) -> int:
    return emit(
        corpus.import_from_recording(
            call_id=args.call_id,
            asset=args.asset,
            license_note=args.license_note,
            channel=args.channel,
            start=args.start,
            duration=args.duration,
            transcript=args.transcript,
            commit_allowed=args.commit_allowed,
        )
    )


def _validate_public(args: argparse.Namespace) -> int:
    return emit(
        corpus.validate_public_audio(
            allowlist=args.allowlist,
            assets_dir=args.assets_dir,
            manifest=args.manifest,
            scenarios_dir=args.scenarios_dir,
        )
    )


def _prompts(args: argparse.Namespace) -> int:
    if args.check:
        return emit(corpus.check_prompts(config=args.config))
    return emit(
        corpus.render_prompts(
            config=args.config,
            output=args.output,
            engine=args.engine,
            force=args.force,
            log=_log,
        )
    )


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "corpus", "scenario audio corpus and IVR prompts")

    build = action(actions, "build", "render missing corpus audio through TTS (paid APIs)", _build)
    build.add_argument("--only", default="", help="build only assets whose name contains this")
    build.add_argument("--force", action="store_true", help="rebuild even if present")
    build.add_argument(
        "--replace-imported",
        action="store_true",
        help="with --force, explicitly replace a captured/imported asset with generated audio",
    )

    imported = action(
        actions, "import", "import a real/public audio asset into the corpus", _import
    )
    imported.add_argument(
        "--asset", required=True, help="existing CORPUS asset filename to replace"
    )
    imported.add_argument("--source", required=True, help="local path or http(s) URL")
    imported.add_argument(
        "--source-type",
        required=True,
        choices=corpus.SOURCE_TYPES,
        help="where this audio came from",
    )
    imported.add_argument("--license-note", required=True)
    imported.add_argument(
        "--transcript",
        default="",
        help="exact transcript for real/imported audio; defaults to the CORPUS text",
    )
    imported.add_argument(
        "--commit-allowed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="whether this audio may be committed to the repo",
    )

    recording = action(
        actions,
        "from-recording",
        "import the callee channel of an S3 call recording as a corpus asset",
        _from_recording,
    )
    recording.add_argument("--call-id", required=True)
    recording.add_argument(
        "--asset",
        "--name",
        dest="asset",
        required=True,
        help="existing CORPUS asset filename to replace",
    )
    recording.add_argument("--channel", type=int, choices=(0, 1), default=1)
    recording.add_argument("--start", type=float, default=0.0, help="trim start (s)")
    recording.add_argument(
        "--duration", type=float, default=0.0, help="trim length (s), 0 = all"
    )
    recording.add_argument(
        "--license-note",
        required=True,
        help="capture consent, ownership, and permitted-use note",
    )
    recording.add_argument(
        "--transcript", default="", help="exact transcript, if different from CORPUS"
    )
    recording.add_argument(
        "--commit-allowed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="whether this recording may be committed to the repository",
    )

    validate = action(
        actions,
        "validate-public",
        "validate a corpus before the public AMD runtime can expose compiled audio",
        _validate_public,
    )
    validate.add_argument(
        "--allowlist",
        type=Path,
        required=True,
        help="version-1 JSON file listing every approved source WAV/MP3 filename",
    )
    validate.add_argument(
        "--assets-dir",
        type=Path,
        default=ASSETS_DIR,
        help="directory containing checksum-bound private source audio",
    )
    validate.add_argument(
        "--manifest", type=Path, default=CORPUS_MANIFEST, help="matching assets_manifest.json"
    )
    validate.add_argument(
        "--scenarios-dir",
        type=Path,
        default=SCENARIOS_DIR,
        help="AMD YAML scenario catalog to validate",
    )

    prompts = action(
        actions,
        "prompts",
        "render the standalone IVR prompts (paid TTS APIs) or validate their source",
        _prompts,
    )
    prompts.add_argument("--config", type=Path, default=None, help="prompt source YAML")
    prompts.add_argument("--output", type=Path, default=None, help="directory for the audio")
    prompts.add_argument("--engine", choices=corpus.PROMPT_ENGINES, default="auto")
    prompts.add_argument("--force", action="store_true")
    prompts.add_argument("--check", action="store_true", help="validate prompt source only")
