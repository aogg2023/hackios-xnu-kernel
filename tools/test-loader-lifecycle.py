#!/usr/bin/env python3
"""Host test for the loader kernel-lifecycle protection.

The marked blocks in xnuloader/loader.c (LC, LC-WRITE, LC-ENTER, LC-WATCH, LC-RETURN,
LC-USB, LC-FB, LC-POLL, LC-EXC) are extracted verbatim and compiled into a stub harness (the C text lives in tools/test-loader-lifecycle-harness.py), so the
checks run production control flow and count production side effects: psci calls, the
log-partition query, block writes, hc_take_back, watchdog pets, kernel-memory reads, USB
sends and the event loop, including the verified-notification rule of the LC-USB and
LC-POLL blocks (statuses, buffer and length matching, the deadline order, the rev6 guards:
a short transfer event stops every owned request, a stopped endpoint answers every later event
with nothing, and only the recorded buffer and length may take over a queued receive). PSCI expectations are raw spec numbers written independently
(TF-A include/lib/psci/psci.h, PSCI_RET_*), asserted against the constants inside the
extracted block, so a wrong constant fails instead of passing on a shared value.
Coverage note: the IRQ save/restore assembly and the note/IRQ-mask passes stay inline in
run_on_other_cpu (assembly needs the target; host checks pin call sites and order) and the
host never executes the restore path (held_irq_masked stays 0). In the exception handler the
one IRQ mask instruction becomes a counted host no-op; the handler itself runs, with the
reset and poll-loop escapes routed through setjmp.
Usage: python3 tools/test-loader-lifecycle.py"""

import importlib.util
import re
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOADER = os.path.join(ROOT, "xnuloader", "loader.c")
EFIH = os.path.join(ROOT, "xnuloader", "efi.h")
BUILD = os.environ.get("TMPDIR", "/tmp")
BLOCKS = ("LC", "LC-WRITE", "LC-ENTER", "LC-WATCH", "LC-RETURN", "LC-USB", "LC-FB", "LC-POLL", "LC-EXC")

def read(path):
    with open(path) as f:
        return f.read()

def extract(src, name):
    m = re.search(r"/\* %s-BEGIN.*?%s-END \*/" % (name, name), src, re.S)
    if not m:
        sys.exit("block %s-BEGIN not found in loader.c" % name)
    return m.group(0)

def macro_value(src, name):
    m = re.search(r"#define\s+%s\s+\(?([0-9xA-Fa-fu]+)\)?" % name, src)
    if not m:
        sys.exit("macro %s not found" % name)
    return m.group(1).rstrip("u")

def struct_copy(src, name):
    m = re.search(r"struct %s \{.*?\n\};" % name, src, re.S)
    if not m:
        sys.exit("struct %s not found" % name)
    return m.group(0)

_spec = importlib.util.spec_from_file_location(
    "lifecycle_harness", os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-loader-lifecycle-harness.py"))
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
PRELUDE = _harness.PRELUDE
SCENARIOS = _harness.SCENARIOS


MUTATIONS = [
	("mut_nowatch_started", "watch_", "\t\t\t*status = OTHER_CPU_NOT_STARTED;\n\t\t\treturn 1;   /* held at once: the charger and the storage are not touched this round */", "\t\t\t*status = OTHER_CPU_NOT_STARTED;"),
	("mut_nowatch_hung", "watch_", "\t\t\t*status = OTHER_CPU_HUNG;\n\t\t\treturn 1;   /* held at once: no block, USB or charger service past the decision */", "\t\t\t*status = OTHER_CPU_HUNG;"),
	("mut_samecore", "same_", "\tkernel_resources_held = cores_off ? 0 : 1;  /* proof or no proof: the only release point */", "\tif (ran_other_core) {\n\t\tkernel_resources_held = cores_off ? 0 : 1;\n\t}"),
	("mut_whitelist", "onissue_", "\treturn status == PSCI_E_NOT_SUPPORTED || status == PSCI_E_INVALID_PARAMS ||\n\t    status == PSCI_E_DENIED || status == PSCI_E_INVALID_ADDRESS;", "\treturn status != 0;"),
	("mut_already_on_const", "psci_spec_", "#define PSCI_E_ALREADY_ON       (-4)", "#define PSCI_E_ALREADY_ON       (-6)"),
	("mut_write_gate", "wlog_", "\tbio = ((kernel_resources_held || firmware_fault) && !active_watch) ? NULL : find_log_partition();", "\tbio = (kernel_resources_held && !active_watch) ? NULL : find_log_partition();"),
	("mut_usb_bound", "usb_held_", "if ((running_live != NULL || kernel_resources_held || firmware_fault) && READ_SYSREG(CNTVCT_EL0) - began > 3 * freq) {", "if (running_live != NULL && READ_SYSREG(CNTVCT_EL0) - began > 3 * freq) {"),
	("mut_enter_early", "bridge_", "\tif (status == 0) {\n\t\t/* the exemption covers the formal watch this answer opens and nothing else: a refused\n\t\t * or unknown start keeps the held gates closed, its log stays in the RAM echo */\n\t\tactive_watch = 1;\n\t}", "\tactive_watch = 1;   /* rev1 bug: open on every answer */"),
	("mut_exc_keep", "exc_", "\tactive_watch = 0;\n\tprint(\"\\nUEFI EXCEPTION", "\tprint(\"\\nUEFI EXCEPTION"),
	("mut_tx_stop", "usb_tx_", "\t\t\t\tusb_stop_endpoint(0, \"unconfirmed at a timeout\");\n", ""),
	("mut_rx_handback", "usb_rx_", "\t\t\t\tusb_rx_queued = 1;   /* the receive keeps its buffer and its place in the queue */\n", ""),
	("mut_poll_guard", "usb_poll_", "\tif (!usb_rx_queued && !usb_out_lost && usb_connected", "\tif (!usb_rx_queued && usb_connected"),
	("mut_unconfirmed", "usb_device_change", "\t\t\t}\n\t\t\t/* the cable moved: readable state or not, nothing in flight is trusted any more */\n\t\t\tusb_lost_on_unconfirmed();\n\t\t\treturn -1;", "\t\t\t}\n\t\t\treturn -1;"),
	("mut_fb_pre", "fb_", "\tfor (; *code && n < sizeof(msg) - 1; code++) {\n\t\tmsg[n++] = (uint8_t)*code;\n\t}\n\tfor (; *text && n < sizeof(msg) - 1; text++) {\n\t\tmsg[n++] = (uint8_t)*text;\n\t}\n\tusb_tx((const uint8_t *)msg, n);", "\tfor (; *code; code++) {\n\t\tusb_tx_buf[n++] = (uint8_t)*code;\n\t}\n\tfor (; *text && n < 63; text++) {\n\t\tusb_tx_buf[n++] = (uint8_t)*text;\n\t}\n\tusb_tx(usb_tx_buf, n);"),
	("mut_ev_active", "usb_notify_", '\tif (data->transfer.status == UsbDeviceTransferStatusActive ||\n\t    data->transfer.status > UsbDeviceTransferStatusCompleteError) {\n\t\treturn 0;   /* the controller may still work on the buffer: no read, no release, no resend */\n\t}', '\tif (data->transfer.status == UsbDeviceTransferStatusActive ||\n\t    data->transfer.status > UsbDeviceTransferStatusCompleteError) {\n\t\tif (out) { usb_out_owned = 0; usb_rx_queued = 0; } else { usb_in_owned = 0; }   /* rev4: every notification ended the request */\n\t\treturn 0;\n\t}'),
	("mut_ev_deadline", "usb_notify_", '\t\tif (msg == UsbDeviceEventTransferNotification) {\n\t\t\tif (size < sizeof(data.transfer)) {\n\t\t\t\tusb_short_event_stops_all();   /* nothing readable names the request it ends */\n\t\t\t\treturn -1;\n\t\t\t}\n\t\t\tif (data.transfer.endpoint_index == endpoint) {\n\t\t\t\t/* a completion in hand is verified before the budget: one arriving at the\n\t\t\t\t * deadline must not be thrown away */\n\t\t\t\tint kept = usb_notification(endpoint == USB_EP_OUT, &data, &bytes);\n\n\t\t\t\tif (kept == 1) {\n\t\t\t\t\treturn (int64_t)bytes;\n\t\t\t\t}\n\t\t\t\tif (kept < 0) {\n\t\t\t\t\treturn -1;    /* usb_notification stopped the endpoint on a bad notification */\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t\t/* with a kernel running, a kernel that may still hold the devices, or a firmware fault,\n\t\t * a host that went away must not keep this core from watching or from reaching the\n\t\t * commands that end the state */\n\t\tif ((running_live != NULL || kernel_resources_held || firmware_fault) && READ_SYSREG(CNTVCT_EL0) - began > 3 * freq) {\n\t\t\tif (usb_out_owned) {\n\t\t\t\tusb_rx_queued = 1;   /* the receive keeps its buffer and its place in the queue */\n\t\t\t}\n\t\t\tif (endpoint == USB_EP_IN && usb_in_owned) {\n\t\t\t\tusb_stop_endpoint(0, "unconfirmed at a timeout");\n\t\t\t}\n\t\t\treturn -1;\n\t\t}\n', '\t\t/* with a kernel running, a kernel that may still hold the devices, or a firmware fault,\n\t\t * a host that went away must not keep this core from watching or from reaching the\n\t\t * commands that end the state */\n\t\tif ((running_live != NULL || kernel_resources_held || firmware_fault) && READ_SYSREG(CNTVCT_EL0) - began > 3 * freq) {\n\t\t\tif (usb_out_owned) {\n\t\t\t\tusb_rx_queued = 1;   /* the receive keeps its buffer and its place in the queue */\n\t\t\t}\n\t\t\tif (endpoint == USB_EP_IN && usb_in_owned) {\n\t\t\t\tusb_stop_endpoint(0, "unconfirmed at a timeout");\n\t\t\t}\n\t\t\treturn -1;\n\t\t}\n\t\tif (msg == UsbDeviceEventTransferNotification) {\n\t\t\tif (size < sizeof(data.transfer)) {\n\t\t\t\tusb_short_event_stops_all();   /* nothing readable names the request it ends */\n\t\t\t\treturn -1;\n\t\t\t}\n\t\t\tif (data.transfer.endpoint_index == endpoint) {\n\t\t\t\t/* a completion in hand is verified before the budget: one arriving at the\n\t\t\t\t * deadline must not be thrown away */\n\t\t\t\tint kept = usb_notification(endpoint == USB_EP_OUT, &data, &bytes);\n\n\t\t\t\tif (kept == 1) {\n\t\t\t\t\treturn (int64_t)bytes;\n\t\t\t\t}\n\t\t\t\tif (kept < 0) {\n\t\t\t\t\treturn -1;    /* usb_notification stopped the endpoint on a bad notification */\n\t\t\t\t}\n\t\t\t}\n\t\t}\n'),
	("mut_ev_lostguard", "usb_guard_", '\tif (out ? usb_out_lost : usb_in_lost) {\n\t\treturn 0;   /* stopped keeps its buffer and its flags: a late event changes nothing */\n\t}\n', ""),
	("mut_ev_shortstop", "usb_guard_", '\tif (usb_out_owned || usb_rx_queued) {\n\t\tusb_stop_endpoint(1, "event too short to attribute");\n\t}\n\tif (usb_in_owned) {\n\t\tusb_stop_endpoint(0, "event too short to attribute");\n\t}', "	(void)0;"),
	("mut_ev_record", "usb_guard_", '\tif (usb_rx_queued) {\n\t\t/* the pending request stays exactly as it was submitted: only a caller using the\n\t\t * recorded buffer and length may take it over */\n\t\tif (buf != usb_out_buf || len != usb_out_len) {\n\t\t\treturn -1;      /* refused: the on-file request and its ownership are untouched */\n\t\t}\n\t\tusb_rx_queued = 0;      /* (every command is received the same way, into the same buffer) */\n\t} else {\n\t\tif (EFI_ERROR(USB->send(USB_EP_OUT, len, buf))) {\n\t\t\treturn -1;\n\t\t}\n\t\tusb_out_owned = 1;      /* the submit record is written only for a send that succeeded */\n\t\tusb_out_buf = buf;      /* the notification is only believed for this buffer and length */\n\t\tusb_out_len = len;\n\t}\n', '\tif (usb_rx_queued) {\n\t\tusb_rx_queued = 0;   /* rev5: any caller took it over and rewrote the metadata */\n\t} else if (EFI_ERROR(USB->send(USB_EP_OUT, len, buf))) {\n\t\treturn -1;\n\t}\n\tusb_out_owned = 1;\n\tusb_out_buf = buf;\n\tusb_out_len = len;\n'),
]

def build_c(src):
    blocks = []
    for name in BLOCKS:
        txt = extract(src, name)
        if name == "LC-ENTER":
            txt = "static void\nrun_enter(void)\n{\n" + txt + "\n}\n"
        elif name == "LC-EXC":
            txt = txt.replace('__asm__ volatile ("msr daifset, #0xf");', "harness_daifset();")
        elif name == "LC-POLL":
            txt = ("static void\npoll_once(void)\n{\n\tuint32_t msg = UsbDeviceEventNoEvent;\n\tuint64_t size = 0, n;\n"
                   "\tusb_device_event_data data;\n\tchar cmd[128];\n\n\tif (USB == NULL) {\n\t\treturn;\n\t}\n"
                   + txt + "\n}\nstatic void usb_poll_command(void) { poll_once(); }\n")
        blocks.append(txt)
    prelude = PRELUDE
    exc = re.search(r"enum \{ EXC_CTX_.*?\};", read(EFIH), re.S).group(0)
    for token, value in (("@START@", macro_value(src, "START_SECONDS")), ("@SILENCE@", macro_value(src, "SILENCE_SECONDS")),
                         ("@SELFTEST@", macro_value(src, "CHECKPOINT_SELFTEST")), ("@MAGIC2@", macro_value(src, "KERNEL_LIVE_MAGIC2")),
                         ("@LIVE@", struct_copy(src, "kernel_live")), ("@EXC@", exc)):
        prelude = prelude.replace(token, value)
    return prelude + "\n" + "\n".join(blocks) + "\n" + SCENARIOS + "\n"

def compile_run(c_text, name):
    cpath = os.path.join(BUILD, "lifecycle-%s.c" % name)
    xpath = os.path.join(BUILD, "lifecycle-%s" % name)
    with open(cpath, "w") as f:
        f.write(c_text)
    r = subprocess.run(["cc", "-std=gnu11", "-O0", "-o", xpath, cpath], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("compile of %s failed:\n%s" % (name, r.stderr))
    r = subprocess.run([xpath], capture_output=True, text=True)
    return r.returncode, r.stdout

def wiring_checks(src):
    fails = []

    def ck(cond, name):
        if not cond:
            fails.append("FAIL wiring_%s" % name)

    def i_of(s, start=0):
        return src.find(s, start)

    ck(src.count("kernel_hold") == 0, "no_old_state_name")
    ck(not re.search(r"\bLC_NONE\b|\bLC_HELD\b|\bLC_UNKNOWN\b", src), "merged_states")
    for gone in ("lc_kernel_may_still_own", "lc_cpu_on_begin", "lc_cpu_on_end", "lc_watch_end", "lc_return_release"):
        ck(gone not in src, "gone_" + gone)
    ck(src.count("#define PSCI_CPU_ON ") == 1, "cpu_on_id_once")
    ck(src.count("static int kernel_resources_held;") == 1, "hold_once")
    ck(src.count("static int active_watch;") == 1, "watch_once")
    blk_e = extract(src, "LC-ENTER")
    ck("status = lc_cpu_on_issue(mpidr, (uint64_t)(uintptr_t)&other_cpu_entry, (uint64_t)(uintptr_t)&other_cpu_start);" in blk_e, "enter_issue")
    ck(0 < blk_e.find("if (status == 0) {") < blk_e.find("active_watch = 1;") < blk_e.find("flush_log();"), "enter_gate_order")
    i_aw0 = i_of("active_watch = 0;")
    i_live = i_of("running_live = NULL;   /* the log pointer drops here")
    ck(0 < i_of("while (status == 0) {") < i_aw0 < i_live, "watch_close_order")
    blk_x = extract(src, "LC-EXC")
    ck(0 < blk_x.find("active_watch = 0;") < blk_x.find("print(\"\\nUEFI EXCEPTION"), "exc_first_act")
    ck(0 < blk_x.find("msr daifset, #0xf") < blk_x.find("firmware_fault = 1;"), "exc_mask_order")
    blk_w = extract(src, "LC-WRITE")
    ck("((kernel_resources_held || firmware_fault) && !active_watch) ? NULL : find_log_partition()" in blk_w, "write_gate_line")
    ck(src.count("find_log_partition()") == 1, "one_partition_query")
    blk_u = extract(src, "LC-USB")
    ck("if ((running_live != NULL || kernel_resources_held || firmware_fault) && READ_SYSREG(CNTVCT_EL0) - began > 3 * freq) {" in blk_u, "usb_bound_line")
    ck("queues a fresh receive" not in src, "no_recovery_overclaim")
    ck(0 < blk_u.find("if (usb_out_lost) {") < blk_u.find("USB->send(USB_EP_OUT, len, buf)"), "rx_guard_first")
    ck(0 < blk_u.find("if (usb_in_lost) {") < blk_u.find("memcpy(usb_tx_buf"), "tx_guard_first")
    ck(blk_u.count("usb_lost_on_unconfirmed();") == 1, "wait_marks_unconfirmed")
    blk_p = extract(src, "LC-POLL")
    ck("!usb_rx_queued && !usb_out_lost && usb_connected" in blk_p, "poll_lost_guard")
    ck("usb_lost_on_unconfirmed();" in blk_p and src.count("usb_lost_on_unconfirmed();") == 2, "poll_marks_unconfirmed")
    ck("usb_notification(1, &data, &n) != 1" in blk_p, "poll_confirms")
    ck("static int\nusb_notification(" in blk_u, "shared_rule_defined")
    ck("usb_notification(endpoint == USB_EP_OUT, &data, &bytes)" in blk_u, "wait_uses_shared_rule")
    ck(0 < blk_u.find("usb_notification(endpoint == USB_EP_OUT") < blk_u.find("began > 3 * freq"), "completion_before_deadline")
    ck(blk_u.count("data_buffer != sub") == 1 and blk_u.count("bytes_completed > sub_len") == 1
       and "data->transfer.status > UsbDeviceTransferStatusCompleteError" in blk_u, "verify_checks_present")
    ck(blk_u.count("usb_out_buf = buf;") == 1 and blk_u.count("usb_in_buf = usb_tx_buf;") == 1
       and "usb_out_buf = usb_rx_buf;" in blk_p, "record_submit")
    ck("a failed call delivered no event to read" in blk_u and "no event to act on" in blk_p, "handle_error_gated")
    ck(0 < blk_u.find("usb_out_lost : usb_in_lost") < blk_u.find("!usb_out_owned"), "lost_guard_first")
    ck(0 <= blk_u.find("if (size < sizeof(data.transfer))") < blk_u.find("data.transfer.endpoint_index =="), "wait_size_before_fields")
    ck(0 <= blk_p.find("if (size < sizeof(data.transfer))") < blk_p.find("data.transfer.endpoint_index !="), "poll_size_before_fields")
    ck(0 <= blk_u.find("if (size >= sizeof(data.device_state))") < blk_u.find("data.device_state =="), "wait_size_before_state")
    ck(0 <= blk_p.find("if (size >= sizeof(data.device_state))") < blk_p.find("data.device_state =="), "poll_size_before_state")
    ck(src.count("usb_short_event_stops_all();") == 2 and "static void\nusb_short_event_stops_all(void)" in blk_u, "short_stop_shared")
    ck("buf != usb_out_buf || len != usb_out_len" in blk_u, "reuse_refuses_foreign")
    blk_f = extract(src, "LC-FB")
    ck(src.count("usb_tx_buf[") == 0, "single_copy_site")
    ck(blk_f.count("n < sizeof(msg) - 1") == 2, "fb_hard_limits")
    ck("usb_tx((const uint8_t *)msg, n);" in blk_f, "fb_via_guarded_tx")
    i_g = i_of("lc_after_return(ran_other_core, cpu_mpidr, cpu_count);")
    ck(0 < i_g < i_of("if (block_write_requested)", i_g), "gate_before_stats")
    ck('set_result(kernel_resources_held ? "returned-held" : "returned", klog_len, 0);' in src, "held_result")
    for s in ('kernel_resources_held ? "hung-held"', 'kernel_resources_held ? "no-such-core-held"',
              'kernel_resources_held ? "selftest-ok-held"', 'kernel_resources_held ? "checkpoint-held"',
              'kernel_resources_held ? "early-exception-held"'):
        ck(s in src, "held_suffix")
    ck('if (!kernel_resources_held) {\n\t\thc_take_back("before the reset");' in src, "reboot_gate")
    ck("lc_cmd_denied(kernel_resources_held, firmware_fault, live, cmd)" in src, "deny_call")
    ck("lc_stop_action(kernel_resources_held, live)" in src, "stop_call")
    ck("AGENTS.md 2" in src[:4000], "overlimit_note")
    blk_watch = extract(src, "LC-WATCH")
    ck("*status = OTHER_CPU_NOT_STARTED;\n\t\t\treturn 1;" in blk_watch, "notstarted_returns")
    ck("*status = OTHER_CPU_HUNG;\n\t\t\treturn 1;" in blk_watch, "hung_returns")
    blk_ret = extract(src, "LC-RETURN")
    ck(0 < blk_ret.find("kernel_resources_held = cores_off ? 0 : 1;") < blk_ret.find('hc_take_back("the kernel came back")'), "release_before_takeback")
    ck(blk_ret.count("msr S3_0_C12_C12_7") == 1, "restore_asm_in_gate")
    ck("if (!kernel_resources_held && held_irq_masked) {" in src, "restore_gated")
    return fails

def main():
    src = read(LOADER)
    rc, out = compile_run(build_c(src), "prod")
    print(out.rstrip())
    if rc != 0:
        sys.exit("production build failed")
    for name, tag, old, new in MUTATIONS:
        patched = src.replace(old, new, 1)
        if patched == src:
            sys.exit("mutation %s: source text changed, update the test" % name)
        rc, mout = compile_run(build_c(patched), name)
        lines = [l for l in mout.splitlines() if l.startswith("FAIL")]
        if rc == 0:
            sys.exit("mutation %s survived: the tests miss it" % name)
        if not any(l.startswith("FAIL " + tag) for l in lines):
            sys.exit("mutation %s failed unexpectedly: %s" % (name, mout))
        print("mutation check: %-22s caught by %d checks (%s...)" % (name, len(lines), lines[0]))
    fails = wiring_checks(src)
    for f in fails:
        print(f)
    if fails:
        sys.exit("wiring checks failed")
    print("wiring checks passed; loader.c is %d lines" % (src.count(chr(10)) + 1))

if __name__ == "__main__":
    main()
