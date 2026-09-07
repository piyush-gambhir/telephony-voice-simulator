"""Dealer-group PBX simulation: declarative config plus a deterministic router.

``domains/pbx.py`` describes four dealership call stories. This package models the
switch itself, so any call can be routed and any transfer can be evaluated.
"""

from .capacity import COSTS, ChannelCost, capacity_report, compare_sites, cost_for
from .dealer_group import VALLEY_AUTO_GROUP, default_group
from .model import CallOutcome, DealerGroup
from .router import CallEvent, CallRequest, CallResult, route, route_internal
from .scenarios import CASES, PbxCase, get_case, run_case, run_suite
from .transfer import (
    TransferAttempt,
    TransferMethod,
    TransferResult,
    attempt_transfer,
    transfer_matrix,
)
from .validation import validate_group

__all__ = [
    "CASES",
    "COSTS",
    "VALLEY_AUTO_GROUP",
    "CallEvent",
    "CallOutcome",
    "CallRequest",
    "CallResult",
    "ChannelCost",
    "DealerGroup",
    "PbxCase",
    "TransferAttempt",
    "TransferMethod",
    "TransferResult",
    "attempt_transfer",
    "capacity_report",
    "compare_sites",
    "cost_for",
    "default_group",
    "get_case",
    "route",
    "route_internal",
    "run_case",
    "run_suite",
    "transfer_matrix",
    "validate_group",
]
