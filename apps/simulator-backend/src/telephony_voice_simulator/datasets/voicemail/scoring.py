"""Score offline AMD predictions against exported voicemail dataset cases."""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...evaluation import silence
from .catalog import Catalog
from .compiler import entry_labels

AMD_LABELS = ("human", "machine-vm", "machine-unavailable", "machine-ivr")
ABSTAIN = "abstain"
PREDICTION_MAP = {label: label for label in AMD_LABELS} | {
    "screener": "machine-ivr",
    "uncertain": ABSTAIN,
    "postpone": ABSTAIN,
}
MACHINE_LABELS = ("machine-vm", "machine-unavailable", "machine-ivr")
CASE_KEYS = ("id", "gold_amd", "callee_class", "category", "difficulty", "language", "split")
GROUPS = ("category", "difficulty", "language", "split")
BASELINE_MAP = {
    "carrier_vm_stock": "machine-vm",
    "business_vm": "machine-vm",
    "personal_vm_firstperson": "machine-vm",
    "mailbox_full_unset": "machine-unavailable",
    "sit_not_in_service": "machine-unavailable",
    "ivr_menu_press": "machine-ivr",
    "screener_saynamer": "machine-ivr",
    "human_greet_then_silent": "human",
    "repeat_confusion": "human",
    "spanish": "uncertain",
    "unclassified": "uncertain",
    "true_silence_no_audio": "uncertain",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: expected an object")
        rows.append(row)
    return rows


def _cases(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for row in rows:
        missing = [key for key in CASE_KEYS if key not in row]
        if missing:
            raise ValueError(f"case {row.get('id')!r}: missing {', '.join(missing)}")
        if row["gold_amd"] not in AMD_LABELS:
            raise ValueError(f"case {row['id']!r}: gold_amd must be one of {', '.join(AMD_LABELS)}")
        if row["id"] in cases:
            raise ValueError(f"duplicate case id {row['id']!r}")
        cases[row["id"]] = row
    return cases


def _predictions(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid, label = row.get("id"), row.get("predicted")
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"prediction without an id: {row!r}")
        if pid in predictions:
            raise ValueError(f"duplicate prediction id {pid!r}")
        if not isinstance(label, str) or label not in PREDICTION_MAP:
            raise ValueError(
                f"prediction {pid!r}: predicted must be one of {', '.join(PREDICTION_MAP)}"
            )
        decided = row.get("decided_at_s")
        if decided is not None and (
            type(decided) not in (int, float) or not math.isfinite(decided) or decided < 0
        ):
            raise ValueError(f"prediction {pid!r}: decided_at_s must be a nonnegative number")
        predictions[pid] = {"predicted": PREDICTION_MAP[label], "decided_at_s": decided}
    return predictions


def _ids(ids: list[str]) -> dict[str, Any]:
    return {"count": len(ids), "ids": sorted(ids)}


def score(
    case_rows: Iterable[dict[str, Any]], prediction_rows: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Accuracy counts an abstention as wrong; cases with no prediction are listed, not scored."""
    cases = _cases(case_rows)
    predictions = _predictions(prediction_rows)
    scored = [(cases[cid], predictions[cid]) for cid in cases if cid in predictions]
    matrix = {gold: dict.fromkeys((*AMD_LABELS, ABSTAIN), 0) for gold in AMD_LABELS}
    groups: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in GROUPS}
    humans_called_machine: list[str] = []
    screeners_called_machine: list[str] = []
    machines_called_human: list[str] = []
    abstentions: list[str] = []
    for case, prediction in scored:
        gold, got = case["gold_amd"], prediction["predicted"]
        matrix[gold][got] += 1
        for name in GROUPS:
            cell = groups[name].setdefault(str(case[name]), {"n": 0, "correct": 0, "abstained": 0})
            cell["n"] += 1
            cell["correct"] += got == gold
            cell["abstained"] += got == ABSTAIN
        if got == ABSTAIN:
            abstentions.append(case["id"])
        elif gold == "human" and got in MACHINE_LABELS:
            humans_called_machine.append(case["id"])
        elif gold != "human" and got == "human":
            machines_called_human.append(case["id"])
        if case["callee_class"] == "screener" and got in ("machine-vm", "machine-unavailable"):
            screeners_called_machine.append(case["id"])
    for table in groups.values():
        for cell in table.values():
            cell["accuracy"] = round(cell["correct"] / cell["n"], 3)
    correct = sum(case["gold_amd"] == prediction["predicted"] for case, prediction in scored)
    decided = [p["decided_at_s"] for _, p in scored if p["decided_at_s"] is not None]
    return {
        "cases": len(cases),
        "scored": len(scored),
        "correct": correct,
        "accuracy": round(correct / len(scored), 3) if scored else None,
        "humans_called_machine": _ids(humans_called_machine),
        "screeners_called_machine": _ids(screeners_called_machine),
        "machines_called_human": _ids(machines_called_human),
        "abstentions": _ids(abstentions),
        "missing_ids": sorted(set(cases) - set(predictions)),
        "extra_ids": sorted(set(predictions) - set(cases)),
        "decided_at_s": (
            {"n": len(decided), "median": round(statistics.median(decided), 3)} if decided else None
        ),
        "confusion_matrix": matrix,
        **{f"by_{name}": dict(sorted(table.items())) for name, table in groups.items()},
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"cases {report['cases']}  scored {report['scored']}  correct {report['correct']}  "
        f"accuracy {report['accuracy']}",
    ]
    for key in (
        "humans_called_machine",
        "screeners_called_machine",
        "machines_called_human",
        "abstentions",
    ):
        item = report[key]
        lines.append(f"{key} {item['count']}: {', '.join(item['ids']) or '-'}")
    lines.append(
        f"missing_ids {len(report['missing_ids'])}: {', '.join(report['missing_ids']) or '-'}"
    )
    lines.append(f"extra_ids {len(report['extra_ids'])}: {', '.join(report['extra_ids']) or '-'}")
    columns = (*AMD_LABELS, ABSTAIN)
    width = max(map(len, columns)) + 2
    lines += ["", "gold \\ predicted".ljust(width) + "".join(c.rjust(width) for c in columns)]
    for gold, row in report["confusion_matrix"].items():
        lines.append(gold.ljust(width) + "".join(str(row[c]).rjust(width) for c in columns))
    for name in GROUPS:
        table = report[f"by_{name}"]
        key_width = max([len(name), *map(len, table)]) + 2
        lines += [
            "",
            name.ljust(key_width)
            + "n".rjust(6)
            + "correct".rjust(9)
            + "abstain".rjust(9)
            + "accuracy".rjust(10),
        ]
        for key, cell in table.items():
            lines.append(
                key.ljust(key_width)
                + str(cell["n"]).rjust(6)
                + str(cell["correct"]).rjust(9)
                + str(cell["abstained"]).rjust(9)
                + f"{cell['accuracy']:.3f}".rjust(10)
            )
    return "\n".join(lines)


def baseline_predictions(catalog: Catalog) -> list[dict[str, Any]]:
    """The callee-transcript regex rules from ``evaluation/silence.py`` over ``opening_text``."""
    rows: list[dict[str, Any]] = []
    for entry in catalog.entries:
        name = silence.bucket(silence.norm(entry_labels(catalog, entry)["opening_text"]))
        rows.append({"id": entry.id, "predicted": BASELINE_MAP[name], "bucket": name})
    return rows
