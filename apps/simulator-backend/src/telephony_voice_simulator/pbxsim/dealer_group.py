"""Valley Auto Group: a three-rooftop dealer group on legacy on-prem switches.

The shape is drawn from how automotive groups actually run their phones:

* Rooftops grew by acquisition, so each store is on a different switch.
* The group centralised a BDC (Business Development Center) that takes sales and
  service-scheduling overflow for every store over tie trunks.
* Service is the highest-volume department and its advisors are almost never at
  the desk, so most service calls fall through coverage. This is the specific
  pain a voice agent is bought to solve.
* The used-car lot has a tiny PRI, so it congests during a weekend sale.
* Ford and Honda have tie trunks to each other and to nothing else, so a Honda
  caller who needs the used lot has to tandem through Ford.

Numbering plan (uniform across the group, dial ``8`` + four digits):

======  ==============  ==================================
Store   Local range     Intersite
======  ==============  ==================================
Ford    ``1xxx``        ``81xxx``
Honda   ``2xxx``        ``82xxx``
Used    ``3xxx``        ``83xxx``
======  ==============  ==================================
"""

from __future__ import annotations

from .model import (
    AgentIntegration,
    BorderElement,
    ClassOfRestriction,
    CoveragePath,
    CoveragePoint,
    DealerGroup,
    Did,
    Extension,
    HuntGroup,
    IvrMenu,
    Mailbox,
    Queue,
    Schedule,
    Site,
    TimeCondition,
    TransferPolicy,
    Trunk,
    minutes,
)

ANSWERING_SERVICE = "+14155550900"

# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #

_SALES = (minutes("09:00"), minutes("20:00"))
_SALES_SAT = (minutes("09:00"), minutes("19:00"))
_SALES_SUN = (minutes("10:00"), minutes("18:00"))
_SERVICE = (minutes("07:00"), minutes("18:00"))
_SERVICE_SAT = (minutes("08:00"), minutes("15:00"))
_BACK_OFFICE = (minutes("08:30"), minutes("17:30"))

_HOLIDAYS = ("01-01", "07-04", "11-27", "12-25")

SCHEDULES = (
    Schedule(
        "sales-hours",
        "Showroom hours",
        {
            "mon": _SALES,
            "tue": _SALES,
            "wed": _SALES,
            "thu": _SALES,
            "fri": _SALES,
            "sat": _SALES_SAT,
            "sun": _SALES_SUN,
        },
        _HOLIDAYS,
    ),
    Schedule(
        "service-hours",
        "Service drive hours",
        {
            "mon": _SERVICE,
            "tue": _SERVICE,
            "wed": _SERVICE,
            "thu": _SERVICE,
            "fri": _SERVICE,
            "sat": _SERVICE_SAT,
            "sun": None,
        },
        _HOLIDAYS,
    ),
    Schedule(
        "back-office-hours",
        "Business office hours",
        {
            "mon": _BACK_OFFICE,
            "tue": _BACK_OFFICE,
            "wed": _BACK_OFFICE,
            "thu": _BACK_OFFICE,
            "fri": _BACK_OFFICE,
            "sat": None,
            "sun": None,
        },
        _HOLIDAYS,
    ),
    Schedule(
        "bdc-hours",
        "Group BDC hours",
        {
            "mon": (minutes("07:00"), minutes("21:00")),
            "tue": (minutes("07:00"), minutes("21:00")),
            "wed": (minutes("07:00"), minutes("21:00")),
            "thu": (minutes("07:00"), minutes("21:00")),
            "fri": (minutes("07:00"), minutes("21:00")),
            "sat": (minutes("08:00"), minutes("19:00")),
            "sun": (minutes("10:00"), minutes("17:00")),
        },
        _HOLIDAYS,
    ),
)

TIME_CONDITIONS = (
    TimeCondition(
        "ford-main-hours",
        "Valley Ford main line",
        "sales-hours",
        "ivr:ford-main",
        "ivr:ford-night",
        "announce:ford-holiday",
    ),
    TimeCondition(
        "ford-service-hours",
        "Valley Ford service line",
        "service-hours",
        "queue:ford-service",
        "vm:ford-service",
        "announce:ford-holiday",
    ),
    TimeCondition(
        "honda-main-hours",
        "Valley Honda main line",
        "sales-hours",
        "ivr:honda-main",
        "ivr:honda-night",
        "announce:honda-holiday",
    ),
    TimeCondition(
        "honda-service-hours",
        "Valley Honda service line",
        "service-hours",
        "hunt:honda-service-advisors",
        "vm:honda-service",
        "announce:honda-holiday",
    ),
    TimeCondition(
        "used-main-hours",
        "Valley Used Car Center main line",
        "sales-hours",
        "ivr:used-main",
        f"external:{ANSWERING_SERVICE}",
        f"external:{ANSWERING_SERVICE}",
    ),
    TimeCondition(
        "bdc-hours-condition",
        "Group BDC line",
        "bdc-hours",
        "queue:bdc",
        "vm:bdc",
        "vm:bdc",
    ),
)

# --------------------------------------------------------------------------- #
# Border elements and trunks
# --------------------------------------------------------------------------- #

BORDER_ELEMENTS = (
    BorderElement(
        "ford-sbc",
        "AudioCodes Mediant 800 (Valley Ford)",
        refer_handling="reject",
        allows_replaces=False,
        media_mode="relay",
        normalizes_dtmf_to="rfc2833",
        routes_bare_extensions=False,
        requires_referred_by=True,
    ),
    BorderElement(
        "honda-cube",
        "Cisco CUBE on ISR 4331 (Valley Honda)",
        refer_handling="consume",
        allows_replaces=True,
        media_mode="relay",
        normalizes_dtmf_to="rfc2833",
        routes_bare_extensions=False,
        requires_referred_by=True,
    ),
    BorderElement(
        "used-gateway",
        "Carrier PRI gateway (Used Car Center)",
        refer_handling="reject",
        allows_replaces=False,
        media_mode="passthrough",
        normalizes_dtmf_to="inband",
        routes_bare_extensions=False,
        requires_referred_by=False,
    ),
)

TRUNKS = (
    Trunk(
        "ford-pri-1",
        "Valley Ford PRI 1 (23B+D)",
        "ford",
        kind="pri",
        signaling="q931",
        channels=23,
        dtmf_mode="q931_keypad",
    ),
    Trunk(
        "ford-sip-1",
        "Valley Ford SIP trunk via SBC",
        "ford",
        kind="sip",
        signaling="sip",
        channels=20,
        dtmf_mode="rfc2833",
    ),
    Trunk(
        "honda-sip-1",
        "Valley Honda SIP trunk via CUBE",
        "honda",
        kind="sip",
        signaling="sip",
        channels=30,
        dtmf_mode="rfc2833",
    ),
    Trunk(
        "used-pri-1",
        "Used Car Center fractional PRI (8B)",
        "used",
        kind="pri",
        signaling="q931",
        channels=8,
        dtmf_mode="q931_keypad",
    ),
    Trunk(
        "tie-ford-honda",
        "Ford <-> Honda 4-wire E&M tie trunk, wink start",
        "ford",
        kind="tie_em",
        signaling="em_wink_start",
        channels=4,
        dtmf_mode="inband",
        peer_site="honda",
        glare_prone=False,
        digit_guard_ms=210,
    ),
    Trunk(
        "tie-ford-used",
        "Ford <-> Used 2-wire E&M tie trunk, immediate start",
        "ford",
        kind="tie_em",
        signaling="em_immediate_start",
        channels=2,
        dtmf_mode="inband",
        peer_site="used",
        glare_prone=True,
        digit_guard_ms=70,
    ),
)

# --------------------------------------------------------------------------- #
# Class of restriction
# --------------------------------------------------------------------------- #

CORS = (
    ClassOfRestriction(
        "cor-station",
        "Standard station",
        allow_external_transfer=True,
        allow_trunk_to_trunk=False,
        allow_intersite=True,
    ),
    ClassOfRestriction(
        "cor-manager",
        "Manager station",
        allow_external_transfer=True,
        allow_trunk_to_trunk=True,
        allow_intersite=True,
    ),
    ClassOfRestriction(
        "cor-restricted",
        "Internal-only station",
        allow_external_transfer=False,
        allow_trunk_to_trunk=False,
        allow_intersite=False,
    ),
    ClassOfRestriction(
        "cor-trunk",
        "Inbound trunk party",
        allow_external_transfer=False,
        allow_trunk_to_trunk=False,
        allow_intersite=True,
    ),
)

# --------------------------------------------------------------------------- #
# Coverage paths
# --------------------------------------------------------------------------- #

COVERAGE_PATHS = (
    CoveragePath(
        "cp-advisor",
        "Service advisor coverage",
        (
            CoveragePoint("busy", "hunt:ford-service-advisors", rings=0),
            CoveragePoint("no_answer", "queue:ford-service", rings=4),
        ),
    ),
    CoveragePath(
        "cp-standard",
        "Standard station coverage",
        (CoveragePoint("all", "vm:station", rings=4),),
    ),
    CoveragePath(
        "cp-operator",
        "Cover to attendant",
        (CoveragePoint("all", "attendant:ford", rings=3),),
    ),
    CoveragePath(
        "cp-manager",
        "Manager coverage: assistant then mailbox",
        (
            CoveragePoint("no_answer", "ext:1001", rings=3),
            CoveragePoint("busy", "vm:station", rings=0),
        ),
    ),
    CoveragePath(
        "cp-honda-advisor",
        "Honda advisor coverage",
        (
            CoveragePoint("busy", "hunt:honda-service-advisors", rings=0),
            CoveragePoint("no_answer", "tie:ford/bdc", rings=4),
        ),
    ),
    CoveragePath(
        "cp-used-standard",
        "Used lot station coverage",
        (CoveragePoint("all", "ext:3000", rings=4),),
    ),
)

# --------------------------------------------------------------------------- #
# Stations
# --------------------------------------------------------------------------- #


def _ext(
    number: str,
    name: str,
    role: str,
    site: str,
    department: str,
    device: str,
    *,
    cor: str = "cor-station",
    state: str = "available",
    coverage: str | None = "cp-standard",
    did: str | None = None,
    bridged: tuple[str, ...] = (),
) -> Extension:
    return Extension(
        number=number,
        name=name,
        role=role,
        site=site,
        department=department,
        device=device,
        cor=cor,
        state=state,  # type: ignore[arg-type]
        coverage=coverage,
        did=did,
        bridged_appearances=bridged,
    )


EXTENSIONS = (
    # --- Valley Ford -------------------------------------------------------- #
    _ext("1000", "Front desk", "Attendant", "ford", "reception", "attendant console",
         coverage="cp-standard"),
    _ext("1001", "Reception 2", "Receptionist", "ford", "reception", "desk phone",
         bridged=("1700",)),
    _ext("1100", "New car floor phone", "Shared", "ford", "sales", "shared phone",
         coverage="cp-operator"),
    _ext("1101", "Sales consultant A", "Sales consultant", "ford", "sales", "desk phone"),
    _ext("1102", "Sales consultant B", "Sales consultant", "ford", "sales", "desk phone",
         state="with_customer"),
    _ext("1110", "Sales manager", "Sales manager", "ford", "sales", "desk phone",
         cor="cor-manager", coverage="cp-manager", state="busy"),
    _ext("1120", "Internet sales", "BDC agent", "ford", "bdc", "headset"),
    _ext("1200", "Service advisor 1", "Service advisor", "ford", "service", "desk phone",
         coverage="cp-advisor", state="with_customer"),
    _ext("1201", "Service advisor 2", "Service advisor", "ford", "service", "desk phone",
         coverage="cp-advisor", state="busy"),
    _ext("1202", "Service advisor 3", "Service advisor", "ford", "service", "desk phone",
         coverage="cp-advisor", state="with_customer"),
    _ext("1210", "Service manager", "Service manager", "ford", "service", "desk phone",
         cor="cor-manager", did="+14155550112"),
    _ext("1220", "Service cashier", "Cashier", "ford", "service", "desk phone"),
    _ext("1230", "Warranty administrator", "Warranty admin", "ford", "service", "desk phone",
         cor="cor-restricted"),
    _ext("1300", "Parts counter", "Parts advisor", "ford", "parts", "shared phone"),
    _ext("1301", "Wholesale parts desk", "Parts advisor", "ford", "parts", "desk phone",
         state="busy"),
    _ext("1400", "Finance office", "F&I manager", "ford", "finance", "desk phone",
         cor="cor-manager", state="busy"),
    _ext("1500", "Title clerk", "Accounting", "ford", "accounting", "desk phone",
         cor="cor-restricted"),
    _ext("1600", "Body shop estimator", "Estimator", "ford", "body", "desk phone",
         state="with_customer"),
    _ext("1700", "General manager", "GM", "ford", "management", "desk phone",
         cor="cor-manager", coverage="cp-manager", did="+14155550110", state="offline"),
    _ext("1800", "Loaner desk", "Rental clerk", "ford", "service", "desk phone"),
    # Group BDC lives at the Ford rooftop but serves all three stores.
    _ext("1900", "BDC agent 1", "BDC agent", "ford", "bdc", "headset"),
    _ext("1901", "BDC agent 2", "BDC agent", "ford", "bdc", "headset", state="busy"),
    _ext("1902", "BDC agent 3", "BDC agent", "ford", "bdc", "headset", state="busy"),
    _ext("1910", "BDC manager", "BDC manager", "ford", "bdc", "desk phone", cor="cor-manager"),
    # Registered voice-agent station.
    _ext("1999", "Voice agent (BDC overflow)", "Virtual agent", "ford", "bdc", "sip registration",
         cor="cor-manager", coverage=None),
    # --- Valley Honda ------------------------------------------------------- #
    _ext("2000", "Front desk", "Attendant", "honda", "reception", "attendant console"),
    _ext("2100", "Sales floor phone", "Shared", "honda", "sales", "shared phone"),
    _ext("2101", "Sales consultant A", "Sales consultant", "honda", "sales", "desk phone",
         state="with_customer"),
    _ext("2110", "Sales manager", "Sales manager", "honda", "sales", "desk phone",
         cor="cor-manager"),
    _ext("2200", "Service advisor 1", "Service advisor", "honda", "service", "desk phone",
         coverage="cp-honda-advisor", state="busy", did="+14155550212"),
    _ext("2201", "Service advisor 2", "Service advisor", "honda", "service", "desk phone",
         coverage="cp-honda-advisor", state="with_customer"),
    _ext("2210", "Service manager", "Service manager", "honda", "service", "desk phone",
         cor="cor-manager"),
    _ext("2300", "Parts counter", "Parts advisor", "honda", "parts", "shared phone"),
    _ext("2400", "Finance office", "F&I manager", "honda", "finance", "desk phone",
         cor="cor-manager"),
    _ext("2700", "General manager", "GM", "honda", "management", "desk phone",
         cor="cor-manager", state="offline"),
    # --- Valley Used Car Center --------------------------------------------- #
    _ext("3000", "Lot office", "Attendant", "used", "reception", "desk phone",
         coverage=None),
    _ext("3100", "Sales floor phone", "Shared", "used", "sales", "shared phone",
         coverage="cp-used-standard", state="with_customer"),
    _ext("3101", "Sales consultant", "Sales consultant", "used", "sales", "desk phone",
         coverage="cp-used-standard", state="busy"),
    _ext("3200", "Service writer", "Service writer", "used", "service", "desk phone",
         coverage="cp-used-standard", state="offline"),
    _ext("3300", "Recon / parts", "Recon coordinator", "used", "parts", "shared phone",
         coverage="cp-used-standard"),
)

# --------------------------------------------------------------------------- #
# Hunt groups, queues, menus, mailboxes
# --------------------------------------------------------------------------- #

HUNT_GROUPS = (
    HuntGroup(
        "ford-service-advisors",
        "Ford service advisors",
        "ford",
        type="ucd",
        members=("1200", "1201", "1202"),
        ring_seconds=24,
        overflow="queue:ford-service",
    ),
    HuntGroup(
        "ford-sales-floor",
        "Ford showroom",
        "ford",
        type="circular",
        members=("1101", "1102", "1100"),
        ring_seconds=20,
        overflow="queue:bdc",
    ),
    HuntGroup(
        "ford-parts",
        "Ford parts counter",
        "ford",
        type="linear",
        members=("1300", "1301"),
        ring_seconds=24,
        overflow="vm:ford-parts",
    ),
    HuntGroup(
        "ford-body",
        "Ford body shop",
        "ford",
        type="linear",
        members=("1600",),
        ring_seconds=24,
        overflow="vm:ford-body",
    ),
    HuntGroup(
        "ford-business-office",
        "Ford business office",
        "ford",
        type="linear",
        members=("1400", "1500"),
        ring_seconds=24,
        overflow="vm:ford-business",
    ),
    HuntGroup(
        "honda-service-advisors",
        "Honda service advisors",
        "honda",
        type="ucd",
        members=("2200", "2201"),
        ring_seconds=24,
        overflow="tie:ford/bdc",
    ),
    HuntGroup(
        "honda-sales-floor",
        "Honda showroom",
        "honda",
        type="circular",
        members=("2101", "2100"),
        ring_seconds=20,
        overflow="tie:ford/bdc",
    ),
    HuntGroup(
        "used-sales-floor",
        "Used lot sales",
        "used",
        type="linear",
        members=("3100", "3101"),
        ring_seconds=24,
        overflow="ext:3000",
    ),
)

QUEUES = (
    Queue(
        "ford-service",
        "Ford service drive queue",
        "ford",
        members=("1200", "1201", "1202", "1220"),
        strategy="ucd",
        sla_seconds=45,
        max_wait_seconds=210,
        overflow="queue:bdc",
        announce_position=True,
    ),
    Queue(
        "bdc",
        "Group BDC queue",
        "ford",
        members=("1900", "1901", "1902", "1120"),
        strategy="ucd",
        sla_seconds=30,
        max_wait_seconds=180,
        overflow="agent:bdc-overflow",
        announce_position=True,
    ),
)

IVR_MENUS = (
    IvrMenu(
        "ford-main",
        "Valley Ford main auto attendant",
        "ford",
        greeting=(
            "Thank you for calling Valley Ford. For sales press 1, service press 2, "
            "parts press 3, the body shop press 4, the business office press 5. "
            "If you know your party's four digit extension you may dial it at any time."
        ),
        options={
            "1": "hunt:ford-sales-floor",
            "2": "time:ford-service-hours",
            "3": "hunt:ford-parts",
            "4": "hunt:ford-body",
            "5": "hunt:ford-business-office",
            "0": "attendant:ford",
        },
        timeout_target="attendant:ford",
        allow_dial_by_extension=True,
        extension_length=4,
    ),
    IvrMenu(
        "ford-night",
        "Valley Ford after-hours attendant",
        "ford",
        greeting=(
            "Thank you for calling Valley Ford. Our showroom is closed. "
            "For the service department press 2, to leave a message for sales press 1, "
            "for roadside assistance press 9."
        ),
        options={
            "1": "vm:ford-sales",
            "2": "vm:ford-service",
            "9": f"external:{ANSWERING_SERVICE}",
        },
        timeout_target="vm:ford-sales",
        max_retries=1,
        allow_dial_by_extension=True,
    ),
    IvrMenu(
        "honda-main",
        "Valley Honda main auto attendant",
        "honda",
        greeting=(
            "Valley Honda. For service press 1, sales press 2, parts press 3, "
            "finance press 4, or stay on the line for an operator."
        ),
        options={
            "1": "time:honda-service-hours",
            "2": "hunt:honda-sales-floor",
            "3": "ext:2300",
            "4": "ext:2400",
            "0": "attendant:honda",
        },
        timeout_target="attendant:honda",
        allow_dial_by_extension=True,
    ),
    IvrMenu(
        "honda-night",
        "Valley Honda after-hours attendant",
        "honda",
        greeting="Valley Honda is closed. Press 1 to leave a message, or hold for our BDC.",
        options={"1": "vm:honda-sales", "2": "tie:ford/bdc"},
        timeout_target="tie:ford/bdc",
        max_retries=1,
    ),
    IvrMenu(
        "used-main",
        "Used Car Center attendant",
        "used",
        greeting="Valley Used Car Center. For sales press 1, for service press 2.",
        options={"1": "hunt:used-sales-floor", "2": "tie:ford/1200"},
        timeout_target="ext:3000",
        max_retries=1,
        allow_dial_by_extension=True,
    ),
)

MAILBOXES = (
    Mailbox("station", "Station mailbox", "ford"),
    Mailbox("ford-sales", "Ford sales mailbox", "ford", notifies="crm"),
    Mailbox("ford-service", "Ford service mailbox", "ford", full=True, notifies="crm"),
    Mailbox("ford-parts", "Ford parts mailbox", "ford"),
    Mailbox("ford-body", "Ford body shop mailbox", "ford"),
    Mailbox("ford-business", "Ford business office mailbox", "ford"),
    Mailbox("bdc", "Group BDC mailbox", "ford", notifies="crm"),
    Mailbox("honda-sales", "Honda sales mailbox", "honda", notifies="crm"),
    Mailbox("honda-service", "Honda service mailbox", "honda", notifies="crm"),
)

# --------------------------------------------------------------------------- #
# Sites
# --------------------------------------------------------------------------- #

SITES = (
    Site(
        "ford",
        "Valley Ford Sales & Service",
        brand="Ford",
        platform="Avaya IP Office 500 V2 R11",
        border_element="ford-sbc",
        main_did="+14155550100",
        numbering_prefix="1",
        attendant="1000",
        trunks=("ford-pri-1", "ford-sip-1", "tie-ford-honda", "tie-ford-used"),
        schedules=("sales-hours", "service-hours", "back-office-hours"),
        transfer_policy=TransferPolicy(
            refer_supported=False,
            refer_replaces=False,
            trunk_to_trunk_transfer=False,
            extensions_dialable_from_trunk=False,
            dtmf_relay="rfc2833",
            codec="g711u",
            attendant_transfer_available=True,
            max_transfer_hops=2,
        ),
        night_target="ivr:ford-night",
        notes=(
            "Hardened SBC rejects inbound REFER and COR blocks trunk-to-trunk transfer. "
            "External parties cannot hand a call to a station; only a station can."
        ),
    ),
    Site(
        "honda",
        "Valley Honda",
        brand="Honda",
        platform="Cisco CUCM 12.5 with CUBE",
        border_element="honda-cube",
        main_did="+14155550200",
        numbering_prefix="2",
        attendant="2000",
        trunks=("honda-sip-1", "tie-ford-honda"),
        schedules=("sales-hours", "service-hours"),
        transfer_policy=TransferPolicy(
            refer_supported=True,
            refer_replaces=True,
            trunk_to_trunk_transfer=False,
            extensions_dialable_from_trunk=False,
            dtmf_relay="rfc2833",
            codec="g711u",
            attendant_transfer_available=True,
            max_transfer_hops=3,
        ),
        night_target="ivr:honda-night",
        notes=(
            "CUBE consumes REFER and performs the transfer itself, so transfers to "
            "internal targets work but the agent gets no NOTIFY progress and cannot "
            "recover a failed handoff. Trunk-to-trunk transfer is still blocked, so the "
            "agent cannot hand a caller to any off-net number."
        ),
    ),
    Site(
        "used",
        "Valley Used Car Center",
        brand="Multi-brand used",
        platform="NEC SV9100",
        border_element="used-gateway",
        main_did="+14155550300",
        numbering_prefix="3",
        attendant="3000",
        trunks=("used-pri-1", "tie-ford-used"),
        schedules=("sales-hours",),
        transfer_policy=TransferPolicy(
            refer_supported=False,
            refer_replaces=False,
            trunk_to_trunk_transfer=False,
            extensions_dialable_from_trunk=False,
            dtmf_relay="inband",
            codec="g729",
            attendant_transfer_available=False,
            max_transfer_hops=1,
        ),
        night_target=f"external:{ANSWERING_SERVICE}",
        notes=(
            "Fractional 8-channel PRI congests during weekend sales events. Inband DTMF "
            "over G.729 destroys digits, so DTMF-driven transfer is unreliable here."
        ),
    ),
)

# --------------------------------------------------------------------------- #
# DID inventory
# --------------------------------------------------------------------------- #

DIDS = (
    Did("+14155550100", "Valley Ford main", "ford", "time:ford-main-hours"),
    Did("+14155550102", "Valley Ford service direct", "ford", "time:ford-service-hours"),
    Did("+14155550103", "Valley Ford parts direct", "ford", "hunt:ford-parts"),
    Did("+14155550110", "Valley Ford GM direct", "ford", "ext:1700"),
    Did("+14155550112", "Valley Ford service manager direct", "ford", "ext:1210"),
    Did(
        "+14155550150",
        "Ford paid-search tracking number",
        "ford",
        "time:ford-main-hours",
        tracking_source="paid_search",
    ),
    Did(
        "+14155550151",
        "Ford service mailer tracking number",
        "ford",
        "time:ford-service-hours",
        tracking_source="direct_mail",
    ),
    Did("+14155550200", "Valley Honda main", "honda", "time:honda-main-hours"),
    Did("+14155550202", "Valley Honda service direct", "honda", "time:honda-service-hours"),
    Did("+14155550212", "Valley Honda advisor direct", "honda", "ext:2200"),
    Did("+14155550300", "Used Car Center main", "used", "time:used-main-hours"),
    Did("+14155550400", "Group BDC", "ford", "time:bdc-hours-condition"),
    Did(
        "+14155550788",
        "Used lot agent hosted number",
        "used",
        "agent:used-lot",
        tracking_source="agent",
    ),
    Did(
        "+14155550777",
        "Voice agent hosted number",
        "ford",
        "agent:service-did",
        tracking_source="agent",
    ),
)

# --------------------------------------------------------------------------- #
# Voice-agent integrations
# --------------------------------------------------------------------------- #

AGENTS = (
    AgentIntegration(
        "bdc-overflow",
        "Agent registered as Ford extension 1999, BDC queue overflow",
        site="ford",
        ingress="registered_extension",
        address="1999",
        reached_via="queue:bdc",
        receives_diversion=False,
        notes=(
            "Inside the dial plan, so bare extensions are addressable and the agent may "
            "use station-side transfer. Best transfer position of the three."
        ),
    ),
    AgentIntegration(
        "service-did",
        "Agent on its own hosted DID, Ford service line forwards to it",
        site="ford",
        ingress="forwarded_with_diversion",
        address="+14155550777",
        reached_via="time:ford-service-hours",
        receives_diversion=True,
        notes=(
            "Outside the dial plan. Diversion header carries the original DID, which is "
            "the only way to tell which rooftop and department the call was for."
        ),
    ),
    AgentIntegration(
        "used-lot",
        "Agent on a hosted DID for the used lot, no SIP relationship",
        site="used",
        ingress="pstn_owned_did",
        address="+14155550788",
        reached_via="did:+14155550788",
        receives_diversion=False,
        notes=(
            "Worst case: outside the dial plan, inband DTMF over G.729, no attendant "
            "transfer, one tie channel pair to the rest of the group."
        ),
    ),
    AgentIntegration(
        "honda-trunk",
        "Agent peered as a SIP trunk to Honda's CUBE",
        site="honda",
        ingress="sip_trunk_peer",
        address="sip:agent@voice.example",
        reached_via="ivr:honda-main",
        receives_diversion=True,
        notes="CUBE consumes REFER, so blind transfer works but progress is invisible.",
    ),
)

VALLEY_AUTO_GROUP = DealerGroup(
    id="valley-auto-group",
    label="Valley Auto Group",
    sites=SITES,
    schedules=SCHEDULES,
    time_conditions=TIME_CONDITIONS,
    trunks=TRUNKS,
    border_elements=BORDER_ELEMENTS,
    cors=CORS,
    coverage_paths=COVERAGE_PATHS,
    extensions=EXTENSIONS,
    hunt_groups=HUNT_GROUPS,
    queues=QUEUES,
    ivr_menus=IVR_MENUS,
    mailboxes=MAILBOXES,
    dids=DIDS,
    agents=AGENTS,
    intersite_prefix="8",
    answering_service=ANSWERING_SERVICE,
)


def default_group() -> DealerGroup:
    return VALLEY_AUTO_GROUP
