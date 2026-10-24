"""Shared parser and wire-comparison assertions for the adreno8 MP_ASSIGN staged-batch log.

The production flush prints the plan header, one line per staged region, the
before/after call lines, and the retain line; the test fakes print one
`scmsnap` group per SCM entry to the SAME stdout stream, so the observed
interleaving is the true event order and this module may assert it directly.
Coverage is the host fixture only: no real kernel, no real SMC, no hardware
cache visibility.
"""
import re

PLAN_RE = re.compile(r"^adreno8: MP_ASSIGN plan: batch (\d+) (table|data), permissions (.+?), "
                     r"argument page ([0-9a-fx]+), (\d+) regions$")
REGION_RE = re.compile(r"^adreno8: MP_ASSIGN plan: batch (\d+) region (\d+) addr ([0-9a-fx]+) "
                       r"len (\d+)$")
BEFORE_RE = re.compile(r"^adreno8: MP_ASSIGN batch (\d+) (table|data): (\d+) regions, (\d+) "
                       r"permission records, (\d+) top-level SCM calls before$")
AFTER_RE = re.compile(r"^adreno8: MP_ASSIGN batch (\d+) (table|data) returned (-?\d+), "
                      r"answer ([0-9a-fx]+), (\d+) top-level SCM calls "
                      r"\(A6 interrupted continuation SMCs not counted\)$")
SNAP_HEAD_RE = re.compile(r"^scmsnap call (\d+) argpage ([0-9a-fx]+) regions_bytes (\d+) "
                          r"sources ([0-9a-fx]+) extended ([0-9a-fx]+) ext ([0-9a-fx]+) "
                          r"([0-9a-fx]+) ([0-9a-fx]+) ([0-9a-fx]+)$")
SNAP_REGION_RE = re.compile(r"^scmsnap call (\d+) region (\d+) addr ([0-9a-fx]+) len (\d+)$")
SNAP_PERM_RE = re.compile(r"^scmsnap call (\d+) perm (\d+) vmid (\d+) perm (\d+) ctx (\d+) "
                          r"ctxsize (\d+) unused (\d+)$")
RETAIN = "adreno8: secure pages retained until cold reset"

# C template both test fixtures insert into their fake SCM. It prints the wire
# records as seen at call entry, from the mapped argument page, to stdout: the
# same stream the production log uses, so the parser above sees the true
# interleaving. {call} is the fixture's current-call expression.
SNAP_C = '''
    /* Logcheck snapshot: wire records as this SCM entry sees them. */
    printf("scmsnap call %u argpage %#llx regions_bytes %llu sources %#llx "
        "extended %#llx ext %#llx %#llx %#llx %#llx\\n", (unsigned){call},
        (unsigned long long)argument0,
        (unsigned long long)argument1, (unsigned long long)argument2,
        (unsigned long long)argument3,
        (unsigned long long)logcheck_snap_ext(argument0)[0],
        (unsigned long long)logcheck_snap_ext(argument0)[1],
        (unsigned long long)logcheck_snap_ext(argument0)[2],
        (unsigned long long)logcheck_snap_ext(argument0)[3]);
    for (unsigned i = 0; i < argument1 / 16; i++) {
        uint64_t *live = (uint64_t *)(uintptr_t)physical_table(argument0);
        printf("scmsnap call %u region %u addr %#llx len %llu\\n", (unsigned){call}, i,
            (unsigned long long)live[2 * i], (unsigned long long)live[2 * i + 1]);
    }
    for (unsigned i = 0; i < logcheck_snap_ext(argument0)[2] / 24; i++) {
        char *perm = (char *)(uintptr_t)physical_table(argument0) + 576
            + 24 * (unsigned long long)i;
        uint32_t vmid, bits, ctx_size, unused;
        uint64_t context;
        memcpy(&vmid, perm, 4);
        memcpy(&bits, perm + 4, 4);
        memcpy(&context, perm + 8, 8);
        memcpy(&ctx_size, perm + 16, 4);
        memcpy(&unused, perm + 20, 4);
        printf("scmsnap call %u perm %u vmid %u perm %u ctx %llu ctxsize %u unused %u\\n",
            (unsigned){call}, i, vmid, bits, (unsigned long long)context, ctx_size, unused);
    }
'''

SNAP_HELPERS_C = '''
static uint64_t *logcheck_snap_ext(uint64_t page_phys) {
    return (uint64_t *)((uintptr_t)physical_table(page_phys) + 640);
}
'''

# Production constants: VMID_HLOS 3, VMID_CP_PIXEL 0xA, PERM_READ 4, PERM_WRITE 2.
PERM_NOTE = {"table": "HLOS read/write and CP_PIXEL read", "data": "CP_PIXEL read/write"}
WIRE_PERM = {"table": [(3, 6), (10, 4)], "data": [(10, 6)]}


def parse(text):
    """Return (events, batches, calls): ordered event kinds, per-batch records, per-call snapshots."""
    events, batches, calls = [], {}, {}
    for line in text.splitlines():
        if line == RETAIN:
            events.append(("retain",))
            continue
        m = PLAN_RE.match(line)
        if m:
            batch = int(m.group(1))
            assert batch not in batches, f"duplicate plan header for batch {batch}"
            assert batch == len(batches), f"batch numbers must ascend from 0: got {batch}"
            batches[batch] = {"role": m.group(2), "perm_note": m.group(3),
                              "argpage": int(m.group(4), 16), "count": int(m.group(5)),
                              "regions": [], "snaps": [], "failed": False}
            events.append(("plan", batch))
            continue
        m = REGION_RE.match(line)
        if m:
            batch = int(m.group(1))
            assert batch in batches, f"region line before its plan header: {line}"
            batches[batch]["regions"].append((m.group(3), int(m.group(4))))
            events.append(("region", batch))
            continue
        m = BEFORE_RE.match(line)
        if m:
            batch = int(m.group(1))
            b = batches.get(batch)
            assert b is not None, f"before line before its plan header: {line}"
            assert b.get("before") is None, f"duplicate before line for batch {batch}"
            b["before"] = {"regions": int(m.group(3)), "perms": int(m.group(4)),
                           "calls": int(m.group(5))}
            assert m.group(2) == b["role"] and int(m.group(3)) == b["count"], line
            events.append(("before", batch))
            continue
        m = AFTER_RE.match(line)
        if m:
            batch = int(m.group(1))
            b = batches.get(batch)
            assert b is not None, f"after line before its plan header: {line}"
            b["after"] = {"status": int(m.group(3)), "answer": int(m.group(4), 16),
                          "calls": int(m.group(5))}
            assert m.group(2) == b["role"], line
            b["failed"] = b["after"]["status"] != 0 or b["after"]["answer"] != 0
            events.append(("after", batch))
            continue
        m = SNAP_HEAD_RE.match(line)
        if m:
            call = {"argpage": int(m.group(2), 16), "regions_bytes": int(m.group(3)),
                    "sources": int(m.group(4), 16), "extended": int(m.group(5), 16),
                    "ext": tuple(int(g, 16) for g in m.groups()[5:]), "regions": [], "perms": []}
            index = int(m.group(1))
            assert index not in calls, f"duplicate snapshot for call {index}"
            calls[index] = call
            events.append(("snap", index))
            continue
        m = SNAP_REGION_RE.match(line)
        if m:
            calls[int(m.group(1))]["regions"].append((m.group(3), int(m.group(4))))
            continue
        m = SNAP_PERM_RE.match(line)
        if m:
            c = calls[int(m.group(1))]
            c["perms"].append((int(m.group(3)), int(m.group(4)), int(m.group(5)),
                               int(m.group(6)), int(m.group(7))))
            continue
    return events, batches, calls


def check(out, page_size=None):
    """Assert the stream order and compare every printed region/permission with the SCM snapshot."""
    events, batches, calls = parse(out)
    assert len(calls) == max(calls, default=-1) + 1 and set(calls) == set(range(len(calls))), \
        "snapshot call numbers must be dense and monotonic"
    open_batch, failed = None, None
    for event in events:
        kind = event[0]
        if kind == "retain":
            continue
        batch = batches[open_batch] if kind == "snap" else batches[event[1]]
        if kind in ("plan", "region", "before", "after"):
            assert failed is None, f"event {kind} for batch {event[1]} after failed batch {failed}"
            if kind == "plan":
                assert open_batch is None, \
                    f"plan for batch {event[1]} while batch {open_batch} is open"
            else:
                assert event[1] == open_batch, \
                    f"{kind} for batch {event[1]} while batch {open_batch} is open"
        if kind == "plan":
            open_batch = event[1]
            assert batch["perm_note"] == PERM_NOTE[batch["role"]], batch
        elif kind == "after":
            assert batch["before"] is not None, f"after without before in batch {event[1]}"
            made = len(batch["snaps"])
            assert batch["after"]["calls"] - batch["before"]["calls"] == made, \
                f"batch {event[1]}: after-before calls must equal its snapshot groups"
            if batch["failed"]:
                failed = event[1]
            open_batch = None
        elif kind == "snap":
            assert open_batch is not None, "snapshot outside an open batch"
            assert batch["before"] is not None, "snapshot before its before line"
            batch["snaps"].append(event[1])
    for index in sorted(calls):
        call = calls[index]
        batch = next(b for b in batches.values() if index in b["snaps"])
        assert call["regions_bytes"] == len(call["regions"]) * 16, call
        assert [call["regions_bytes"]] == [batch["count"] * 16], "SCM region bytes must match log"
        assert call["regions"] == batch["regions"], f"call {index} wire regions differ from log"
        assert call["argpage"] == batch["argpage"], "SCM argument page differs from log header"
        assert call["sources"] == call["argpage"] + 512, call
        assert call["extended"] == call["argpage"] + 640, call
        perm_bytes = len(WIRE_PERM[batch["role"]]) * 24
        assert call["ext"] == (4, call["argpage"] + 576, perm_bytes, 0), call
        assert call["ext"] == (4, call["argpage"] + 576,
                               batch["before"]["perms"] * 24, 0)
        assert [(p[0], p[1]) for p in call["perms"]] == WIRE_PERM[batch["role"]], call
        assert all(p[2:] == (0, 0, 0) for p in call["perms"]), call
    for batch, b in sorted(batches.items()):
        assert [i for i, _ in enumerate(b["regions"])] == list(range(b["count"])), batch
        assert b["after"] is not None or failed == batch, "open batch without after line"
        if page_size is not None:
            for _addr, length in b["regions"]:
                assert length % page_size == 0, (batch, length)
    return batches, calls, failed
