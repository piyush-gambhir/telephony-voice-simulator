"""Validate a dealer dial plan before simulating calls against it.

Errors include the owning object and reference, so custom groups can be checked
without placing calls through every possible branch. Cycles are permitted here:
the router reports them as simulated call outcomes.
"""

from __future__ import annotations

from .model import DealerGroup, WEEKDAYS


def validate_group(group: DealerGroup) -> tuple[str, ...]:
    errors: list[str] = []
    buckets = {
        "site": (group.sites, "id"),
        "schedule": (group.schedules, "id"),
        "time": (group.time_conditions, "id"),
        "trunk": (group.trunks, "id"),
        "border": (group.border_elements, "id"),
        "cor": (group.cors, "id"),
        "coverage": (group.coverage_paths, "id"),
        "ext": (group.extensions, "number"),
        "hunt": (group.hunt_groups, "id"),
        "queue": (group.queues, "id"),
        "ivr": (group.ivr_menus, "id"),
        "vm": (group.mailboxes, "id"),
        "did": (group.dids, "number"),
        "agent": (group.agents, "id"),
    }
    indexes = {}
    for kind, (items, key) in buckets.items():
        index = {}
        for item in items:
            identifier = getattr(item, key)
            if identifier in index:
                errors.append(f"duplicate {kind}: {identifier}")
            if not identifier:
                errors.append(f"{kind}: empty identifier")
            index[identifier] = item
        indexes[kind] = index

    def reference(kind: str, value: str, owner: str) -> None:
        if value not in indexes[kind]:
            errors.append(f"{owner}: unknown {kind} '{value}'")

    def number(value: int, owner: str, minimum: int = 0) -> None:
        if type(value) is not int or value < minimum:
            errors.append(f"{owner}: expected an integer >= {minimum}")

    def destination(ref: str, owner: str) -> None:
        if ref in {"busy", "hangup"}:
            return
        kind, _, value = ref.partition(":")
        if kind == "attendant":
            reference("site", value, owner)
        elif kind == "vm":
            box, _, station = value.partition("/")
            reference("vm", box, owner)
            if station:
                reference("ext", station, owner)
        elif kind == "tie":
            site, _, target = value.partition("/")
            reference("site", site, owner)
            found = next(
                (indexes[k].get(target) for k in ("ext", "hunt", "queue") if target in indexes[k]),
                None,
            )
            if found is None or found.site != site:
                errors.append(f"{owner}: unknown tie target '{ref}' at site '{site}'")
        elif kind in {"external", "announce"}:
            if not value:
                errors.append(f"{owner}: empty destination '{ref}'")
        elif kind in {"ext", "hunt", "queue", "ivr", "time", "agent"}:
            reference(kind, value, owner)
        else:
            errors.append(f"{owner}: unknown destination '{ref}'")

    for site in group.sites:
        if site.border_element:
            reference("border", site.border_element, site.id)
        reference("ext", site.attendant, site.id)
        reference("did", site.main_did, site.id)
        for trunk in site.trunks:
            reference("trunk", trunk, site.id)
        for schedule in site.schedules:
            reference("schedule", schedule, site.id)
        destination(site.night_target, site.id)
        number(site.transfer_policy.max_transfer_hops, f"{site.id}.max_transfer_hops", 1)
    for trunk in group.trunks:
        reference("site", trunk.site, trunk.id)
        number(trunk.channels, f"{trunk.id}.channels", 1)
        if trunk.peer_site:
            reference("site", trunk.peer_site, trunk.id)
    for schedule in group.schedules:
        for day, window in schedule.hours.items():
            if day not in WEEKDAYS:
                errors.append(f"{schedule.id}: unknown weekday '{day}'")
            if window is not None and (
                len(window) != 2
                or any(type(v) is not int for v in window)
                or not 0 <= window[0] < 1440
                or not 0 <= window[1] <= 1440
                or window[0] == window[1]
            ):
                errors.append(f"{schedule.id}.{day}: invalid opening window {window}")
    for condition in group.time_conditions:
        reference("schedule", condition.schedule, condition.id)
        for ref in (condition.open_target, condition.closed_target, condition.holiday_target):
            if ref:
                destination(ref, condition.id)
    for path in group.coverage_paths:
        for point in path.points:
            destination(point.target, path.id)
            number(point.rings, f"{path.id}.rings")
    for station in group.extensions:
        reference("site", station.site, station.number)
        reference("cor", station.cor, station.number)
        if station.coverage:
            reference("coverage", station.coverage, station.number)
        if station.did:
            reference("did", station.did, station.number)
            did = indexes["did"].get(station.did)
            if did and (did.site != station.site or did.target != f"ext:{station.number}"):
                errors.append(f"{station.number}: DID {station.did} routes to another station")
        for appearance in station.bridged_appearances:
            reference("ext", appearance, station.number)
    for item in (*group.hunt_groups, *group.queues):
        reference("site", item.site, item.id)
        for member in item.members:
            reference("ext", member, item.id)
        destination(item.overflow, item.id)
    for hunt in group.hunt_groups:
        number(hunt.ring_seconds, f"{hunt.id}.ring_seconds")
    for queue in group.queues:
        number(queue.sla_seconds, f"{queue.id}.sla_seconds")
        number(queue.max_wait_seconds, f"{queue.id}.max_wait_seconds")
    for menu in group.ivr_menus:
        reference("site", menu.site, menu.id)
        for ref in (*menu.options.values(), menu.timeout_target):
            destination(ref, menu.id)
        if menu.invalid_target != "repeat":
            destination(menu.invalid_target, menu.id)
        number(menu.max_retries, f"{menu.id}.max_retries")
        number(menu.timeout_seconds, f"{menu.id}.timeout_seconds")
    for item in (*group.mailboxes, *group.agents, *group.dids):
        reference("site", item.site, getattr(item, "id", getattr(item, "number", "")))
    for did in group.dids:
        destination(did.target, did.number)
    return tuple(errors)


def require_valid_group(group: DealerGroup) -> None:
    errors = validate_group(group)
    if errors:
        raise ValueError("Invalid dealer group:\n" + "\n".join(errors))
