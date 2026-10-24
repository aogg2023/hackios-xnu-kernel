"""Embedded C text for tools/test-loader-lifecycle.py.

PRELUDE is the stub firmware harness (fake clock, captured prints, counting psci/storage/USB
stubs) and SCENARIOS the scenario main() that runs the extracted production blocks against
them. The two strings are concatenated with the extracted loader.c blocks into one C file, so
they only compile as part of that file; this module is pure data, read verbatim by the driver
and loaded by path from it. Split out to keep each file under the 800-line limit (AGENTS.md 2).
"""

PRELUDE = r"""#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>
/* ---- harness scaffolding: fake clock, captured prints, counted firmware stubs ---- */
static uint64_t clk_now, clk_freq = 1000000000ULL;
#define CNTVCT_EL0 0
#define CNTFRQ_EL0 1
static uint64_t clock_value(int which) { return which == CNTFRQ_EL0 ? clk_freq : clk_now; }   /* fake clock for READ_SYSREG */
#define READ_SYSREG(r) clock_value(r)
#define EFIAPI
@EXC@
static char out_cap[1 << 16];
static size_t out_len;
static int fail_count;
static int64_t print(const char *fmt, ...)
{
	va_list ap;
	int n;
	va_start(ap, fmt);
	n = vsnprintf(out_cap + out_len, sizeof(out_cap) - out_len, fmt, ap);
	va_end(ap);
	if (n > 0 && (out_len += (size_t)n) >= sizeof(out_cap)) {
		out_len = sizeof(out_cap) - 1;
	}
	return n;
}
static void put_char(char c) { if (out_len < sizeof(out_cap) - 1) { out_cap[out_len++] = c; } }
static int cap_has(const char *s) { return strstr(out_cap, s) != NULL; }
static void cap_reset(void) { out_cap[0] = 0; out_len = 0; }
#define CK(cond, name) do { if (!(cond)) { printf("FAIL %s\n", name); fail_count++; } } while (0)
#define CK0(name) do { printf("FAIL %s\n", name); fail_count++; } while (0)
#define START_SECONDS @START@
#define SILENCE_SECONDS @SILENCE@
#define CHECKPOINT_SELFTEST @SELFTEST@
#define KERNEL_LIVE_MAGIC2 @MAGIC2@
@LIVE@
/* platform and run_on_other_cpu context for the LC-ENTER bridge */
struct platform { int log_other_cpu; };
static const struct platform platform_annibale = { 1 };
static const struct platform plat_two = { 1 };
static const struct platform *PLAT;
static int screen_quiet;
static int firmware_fault;
static uint64_t mpidr = 0x81000000ULL, started, last_pet, last_battery, last_beat_at;
static int64_t status;
static char other_cpu_entry, other_cpu_start;
static void usb_poll_command(void);
/* psci: scripted answer, captured call, per-core affinity list (raw ids from the spec) */
#define PSCI_AFFINITY_INFO 0xC4000004u
static int psci_calls, psci_aff_idx;
static uint64_t psci_fn, psci_a1, psci_a2, psci_a3;
static int64_t psci_next, psci_aff[8];
static int64_t psci_call(uint64_t f, uint64_t a1, uint64_t a2, uint64_t a3)
{
	psci_calls++;
	psci_fn = f, psci_a1 = a1, psci_a2 = a2, psci_a3 = a3;
	if (f == PSCI_AFFINITY_INFO) {
		return psci_aff[psci_aff_idx++];
	}
	return psci_next;
}
static void flush_dcache(uint64_t a, uint64_t b) { (void)a; (void)b; }
static void clean_dcache(uint64_t a, uint64_t b) { (void)a; (void)b; }
/* ---- stubs for LC-WRITE ---- */
typedef uint64_t efi_status;
#define EFI_ERROR(s) (((int64_t)(s)) < 0)
struct h_media { uint64_t block_size, io_align, media_id; };
struct h_bio {
	struct h_media *media;
	efi_status (*read_blocks)(struct h_bio *, uint64_t, uint64_t, uint64_t, void *);
	efi_status (*write_blocks)(struct h_bio *, uint64_t, uint64_t, uint64_t, void *);
	efi_status (*flush_blocks)(struct h_bio *);
};
typedef struct h_bio efi_block_io;
static char out_buf[8192], block_buffer[16384];
static uint32_t staged_len;
static int compose_calls, find_calls, w_blocks, r_blocks, flushes;
static int bio_present, uefi_list = 1, hc_log_skips, hc_known;
static struct h_media h_media = { 512, 0, 7 };
static efi_status h_write(efi_block_io *b, uint64_t m, uint64_t lba, uint64_t bytes, void *buf)
{ (void)b; (void)m; (void)lba; (void)buf; w_blocks += (int)(bytes / 512); return 0; }
static efi_status h_read(efi_block_io *b, uint64_t m, uint64_t lba, uint64_t bytes, void *buf)
{
	(void)m; (void)lba;
	if (bytes <= sizeof(block_buffer)) { memcpy(buf, out_buf, bytes); }
	r_blocks++;
	return 0;
}
static efi_status h_flush(efi_block_io *b) { (void)b; flushes++; return 0; }
static efi_block_io h_bio = { &h_media, h_read, h_write, h_flush };
static efi_block_io *find_log_partition(void) { find_calls++; return bio_present ? (efi_block_io *)&h_bio : NULL; }
static void compose_logs(const char *k, uint64_t l) { (void)k; (void)l; compose_calls++; staged_len = 100; }
static int hc_list_is_uefis(void) { return uefi_list; }
static void hc_print_state(const char *w) { (void)w; }
static int skip_ufs_arbitration = 1;
static void write_logs(const char *klog, uint64_t klog_len);
static void flush_log(void) { write_logs(NULL, 0); }
/* ---- stubs for LC-WATCH ---- */
static volatile uint64_t other_cpu_done, other_cpu_stage;
enum { CTX_LOG_PA = 0, CTX_KSP = 10 };
static uint64_t loader_ctx[16];
static uint64_t seconds_since(uint64_t then) { return (clk_now - then) / clk_freq; }
static int wdt_armed = 1, pets, charger_polls, block_polls, hung_snaps, charger_enabled;
static void wdt_pet(void) { pets++; }
static void charger_poll(void) { charger_polls++; }
static void block_poll(void) { block_polls++; }
static void log_of_hung_kernel(const struct kernel_live *l, uint64_t s) { (void)l; (void)s; hung_snaps++; }
/* ---- stubs for LC-RETURN ---- */
static const char *takebacks[8];
static int takebacks_n;
static void hc_take_back(const char *when) { if (takebacks_n < 8) { takebacks[takebacks_n++] = when; } }
static struct { struct { uint64_t switched_in, switched_out; } kernel_native; } block_shared;
static uint64_t held_daif, held_group1;
static int held_irq_masked;
static const char *recording_data;
static uint64_t recording_len, sound_len;
static const char *sound_data;
/* ---- stubs for LC-USB / LC-POLL / LC-EXC ---- */
#define USB_EP_OUT 0x01
#define USB_EP_IN  0x81
static uint8_t rx_store[1024], tx_store[1024];
static uint8_t *usb_rx_buf = rx_store, *usb_tx_buf = tx_store;
enum { UsbDeviceEventNoEvent = 0, UsbDeviceEventDeviceStateChange = 1, UsbDeviceEventTransferNotification = 2 };
enum { UsbDeviceStateDisconnected = 0, UsbDeviceStateConnected = 1 };
enum { UsbDeviceTransferStatusActive = 0, UsbDeviceTransferStatusCompleteOK = 1, UsbDeviceTransferStatusCancelled = 2,
       UsbDeviceTransferStatusCompleteError = 3 };   /* raw values from xnuloader/efi.h */
typedef union usb_device_event_data {
	uint32_t device_state;
	struct { uint32_t status; uint8_t endpoint_index; uint64_t bytes_completed; void *data_buffer; } transfer;
} usb_device_event_data;
struct h_usb {
	efi_status (*handle_event)(uint32_t *msg, uint64_t *size, usb_device_event_data *data);
	efi_status (*send)(uint8_t endpoint, uint64_t len, void *buf);
};
static struct h_usb *USB;
static int usb_connected = 1, block_enabled = 1;
static const struct kernel_live *running_live;
static int ev_calls, ev_sends, ev_after, ev_change, ev_msg, ev_change_state, ev_err, ev_status = -1;
static uint8_t ev_ep = USB_EP_IN;
static uint64_t ev_bytes = 4096, ev_step, ev_size, ev_no_events;
static int ev_size_set;   /* without it the call reports a full payload; with it even size 0 is explicit */
static void *ev_out_buf, *ev_in_buf, *ev_buf;   /* ev_status -1 = CompleteOK, ev_buf 0 = the sent buffer */
static efi_status ev_handle(uint32_t *msg, uint64_t *size, usb_device_event_data *data)
{
	ev_calls++;
	clk_now += ev_step;
	if (ev_err) {
		return 1;   /* the call itself failed: a correct consumer must read none of the outputs */
	}
	/* the cap keeps a mutation from hanging: past it every event reports a link loss */
	if (ev_change || (ev_after && ev_calls > ev_after) || ev_calls > 400) {
		*msg = UsbDeviceEventDeviceStateChange;
		*size = sizeof(*data);
		data->device_state = (uint32_t)(ev_change ? ev_change_state : UsbDeviceStateDisconnected);
		return 0;
	}
	if (ev_no_events && ev_calls <= ev_no_events) {
		*msg = UsbDeviceEventNoEvent;
		*size = 0;
		return 0;
	}
	*msg = (uint32_t)ev_msg;
	*size = ev_size_set ? ev_size : sizeof(*data);
	if (ev_msg == UsbDeviceEventTransferNotification) {
		data->transfer.status = ev_status < 0 ? UsbDeviceTransferStatusCompleteOK : (uint32_t)ev_status;
		data->transfer.endpoint_index = ev_ep;
		data->transfer.bytes_completed = ev_bytes;
		data->transfer.data_buffer = ev_buf ? ev_buf : (ev_ep == USB_EP_OUT ? ev_out_buf : ev_in_buf);
	}
	return 0;
}
static efi_status ev_send(uint8_t endpoint, uint64_t len, void *buf)
{
	ev_sends++;
	if (endpoint == USB_EP_OUT) { ev_out_buf = buf; } else { ev_in_buf = buf; }
	(void)len;
	return 0;
}
static struct h_usb h_usb = { ev_handle, ev_send };
static jmp_buf jb_exit;
static int irq_mask_calls;
static void harness_daifset(void) { irq_mask_calls++; }   /* counts without masking: EL0 cannot run msr */
static char fb_got[128];
static int fb_calls, fb_armed;
static void fb_command(const char *cmd)
{
	fb_calls++;
	strncpy(fb_got, cmd, sizeof(fb_got) - 1);
	if (fb_armed) { longjmp(jb_exit, 1); }
}
static char last_result[64];
static void set_result(const char *what, uint64_t a, uint64_t b) { snprintf(last_result, sizeof(last_result), "%s %d %x", what, (int)a, (unsigned)b); }
static char __ImageBase;
static int res_calls;
struct h_rt { efi_status (*reset_system)(int, unsigned, const void *, const void *); };
struct h_st { struct h_rt *rt; };
static efi_status h_reset(int a, unsigned b, const void *c, const void *d)
{ (void)a; (void)b; (void)c; (void)d; res_calls++; longjmp(jb_exit, 2); return 0; }
static struct h_rt h_rt = { h_reset };
static struct h_st h_st = { &h_rt };
#define ST (&h_st)

"""

SCENARIOS = r"""/* ---- scenarios ---- */
static const char *const blocked_cmds[] = { "oem ramdisk x", "download:1f000", "oem run", "oem selftest", "oem hang", "oem recording", "oem sound", "oem clearscreen", "oem memmap", "oem probe", "oem logwrite" };
static const char *const open_cmds[] = { "getvar:product", "getvar:result", "upload", "oem log", "oem screenshot", "reboot", "oem stop", "" };
static void sc_psci_spec(void)
{
	/* raw numbers from the SMC Calling Convention, written here without the loader macros */
	struct { int64_t got, want; const char *tag; } v[] = {
		{ PSCI_E_NOT_SUPPORTED, -1, "psci_spec_not_supported" }, { PSCI_E_INVALID_PARAMS, -2, "psci_spec_invalid_params" },
		{ PSCI_E_DENIED, -3, "psci_spec_denied" }, { PSCI_E_ALREADY_ON, -4, "psci_spec_already_on" },
		{ PSCI_E_ON_PENDING, -5, "psci_spec_on_pending" }, { PSCI_E_INTERNAL_FAILURE, -6, "psci_spec_internal_failure" },
		{ PSCI_E_NOT_PRESENT, -7, "psci_spec_not_present" }, { PSCI_E_DISABLED, -8, "psci_spec_disabled" },
		{ PSCI_E_INVALID_ADDRESS, -9, "psci_spec_invalid_address" } };
	for (unsigned i = 0; i < sizeof(v) / sizeof(v[0]); i++) {
		CK(v[i].got == v[i].want, v[i].tag);
	}
	CK(PSCI_CPU_ON == 0xC4000003u, "psci_spec_cpu_on_id");
	CK(PSCI_AFFINITY_OFF == 1, "psci_spec_affinity_off");
	CK(USB_EP_OUT == 0x01 && USB_EP_IN == 0x81, "spec_usb_eps");
}
static void one_issue(int64_t answer, int64_t want_held, const char *tag)
{
	kernel_resources_held = 0; psci_next = answer;
	int64_t got = lc_cpu_on_issue(0x81000000ULL, 0xdead0000ULL, 0xbeef0000ULL);
	CK(got == answer, tag);
	CK(kernel_resources_held == want_held, tag);
}
static void sc_cpu_on(void)
{
	one_issue(0, 1, "onissue_ok"); one_issue(-1, 0, "onissue_neg1"); one_issue(-2, 0, "onissue_neg2");
	one_issue(-3, 0, "onissue_neg3"); one_issue(-9, 0, "onissue_neg9");
	one_issue(-4, 1, "onissue_neg4_already_on"); one_issue(-5, 1, "onissue_neg5_on_pending");
	one_issue(-6, 1, "onissue_neg6_internal"); one_issue(-7, 1, "onissue_neg7_not_present");
	one_issue(-8, 1, "onissue_neg8_disabled"); one_issue(-99, 1, "onissue_neg99_unknown"); one_issue(7, 1, "onissue_pos");
	kernel_resources_held = 0;
	psci_next = 0;
	lc_cpu_on_issue(0x81000000ULL, 0x1000ULL, 0x2000ULL);
	CK(psci_fn == 0xC4000003u, "onissue_fn");
	CK(psci_a1 == 0x81000000ULL && psci_a2 == 0x1000ULL && psci_a3 == 0x2000ULL, "onissue_args");
	one_issue(-2, 0, "onissue_refused_reopens");
	CK(!lc_cmd_denied(kernel_resources_held, 0, 0, "download:1f000"), "onissue_refused_download_open");
	CK(!lc_cmd_denied(kernel_resources_held, 0, 0, "oem stop"), "onissue_refused_stop_open");
	one_issue(-4, 1, "onissue_already_on_stays");
	CK(lc_cmd_denied(kernel_resources_held, 0, 0, "download:1f000"), "onissue_already_on_download_denied");
	CK(lc_cmd_denied(kernel_resources_held, 0, 0, "oem run"), "onissue_already_on_run_denied");
}
static void sc_commands(void)
{
	for (unsigned i = 0; i < sizeof(blocked_cmds) / sizeof(blocked_cmds[0]); i++) {
		CK(lc_cmd_denied(1, 0, 0, blocked_cmds[i]), "held_block_cmd");
		CK(!lc_cmd_denied(0, 0, 0, blocked_cmds[i]), "free_open_cmd");
	}
	for (unsigned i = 0; i < sizeof(open_cmds) / sizeof(open_cmds[0]); i++) {
		CK(!lc_cmd_denied(1, 0, 0, open_cmds[i]), "held_open_cmd");
	}
	CK(lc_cmd_denied(1, 0, 1, "download:1f000"), "live_block_cmd");
	CK(lc_cmd_denied(0, 1, 0, "download:1f000"), "fault_block_cmd");
	CK(lc_busy(1, 0, 0), "busy_held");
	CK(lc_busy(0, 0, 1), "busy_live");
	CK(!lc_busy(0, 0, 0), "busy_free");
	CK(lc_stop_action(1, 0) == LC_STOP_HELD, "stop_held_honest");
	CK(lc_stop_action(0, 1) == LC_STOP_NOTIFY, "stop_live_notifies");
	CK(lc_stop_action(0, 0) == LC_STOP_IDLE, "stop_idle_okay");
	kernel_resources_held = 1;
	clk_now = 12345;
	lc_cmd_denied(kernel_resources_held, 0, 0, "oem run");
	CK(kernel_resources_held == 1 && clk_now == 12345, "decide_pure");
}
/* the real bridge: request, exemption window, the log_other_cpu flush, before the loop */
static void sc_enter(void)
{
	PLAT = &platform_annibale;   /* log_other_cpu = 1, the reachable annibale case */
	psci_next = -4;
	kernel_resources_held = active_watch = 0;
	bio_present = 1;
	find_calls = w_blocks = compose_calls = 0; cap_reset();
	run_enter();
	CK(status == -4, "bridge_refused_status");
	CK(kernel_resources_held == 1, "bridge_refused_holds");
	CK(active_watch == 0, "bridge_refused_no_exemption");
	CK(find_calls == 0 && w_blocks == 0, "bridge_refused_no_storage");
	CK(compose_calls == 1 && cap_has("the firmware answers") && !cap_has("KERNEL LOG"), "bridge_refused_stages_only");
	psci_next = kernel_resources_held = 0;
	find_calls = w_blocks = 0; cap_reset();
	run_enter();
	CK(status == 0 && active_watch == 1, "bridge_ok_watch_opens");
	CK(find_calls == 1 && w_blocks > 0, "bridge_ok_flush_writes");
	active_watch = 0;
}
static uint64_t last_pet_v, last_batt_v, last_beat_v, last_beat_at_v;
static int arrived_v;
static int64_t status_v;
static int wr(struct kernel_live *l, uint64_t began)
{
	return lc_watch_round(l, 0, began, 60, &arrived_v, &last_pet_v, &last_batt_v, &last_beat_v, &last_beat_at_v, &status_v);
}
static void watch_reset(uint64_t t)
{
	other_cpu_done = 0;
	other_cpu_stage = arrived_v = 1;
	last_pet_v = last_batt_v = last_beat_v = last_beat_at_v = clk_now = t;
	status_v = pets = charger_polls = block_polls = hung_snaps = 0;
	ev_calls = ev_sends = ev_after = ev_change = ev_change_state = 0;
	ev_msg = UsbDeviceEventNoEvent;
	ev_step = 0; USB = &h_usb;
	usb_connected = 1;
}
static void sc_watch(void)
{
	static struct kernel_live live;
	live.heartbeat = 100;
	kernel_resources_held = 1;   /* as after an accepted CPU_ON */
	watch_reset(1000ULL * clk_freq);
	for (int r = 0; r < 3; r++) {
		live.heartbeat += 1;
		clk_now += 2 * clk_freq;
		CK(wr(&live, 1000ULL * clk_freq) == 0, "watch_normal_round");
	}
	CK(block_polls >= 3 && status_v == 0, "watch_normal_services");
	watch_reset(2000ULL * clk_freq);
	other_cpu_done = 1;
	CK(wr(&live, 2000ULL * clk_freq) == 1, "watch_done_stops");
	CK(status_v == 0 && block_polls == 0 && charger_polls == 0 && ev_calls == 0, "watch_done_no_services");
	watch_reset(3000ULL * clk_freq);
	other_cpu_stage = 0;
	arrived_v = 0;
	charger_enabled = 1;
	clk_now += (START_SECONDS + 1) * clk_freq;
	CK(wr(&live, 3000ULL * clk_freq) == 1, "watch_notstarted_stops");
	CK(status_v == OTHER_CPU_NOT_STARTED, "watch_notstarted_status");
	CK(block_polls == 0 && charger_polls == 0 && ev_calls == 0 && pets == 0, "watch_notstarted_no_services");
	charger_enabled = 0;
	watch_reset(4000ULL * clk_freq);
	CK(wr(&live, 4000ULL * clk_freq) == 0, "watch_hung_first_round");
	block_polls = charger_polls = ev_calls = 0;
	/* the heartbeat stays where the first round recorded it: the kernel said nothing more */
	clk_now += (SILENCE_SECONDS + 1) * clk_freq;
	CK(wr(&live, 4000ULL * clk_freq) == 1, "watch_hung_stops");
	CK(status_v == OTHER_CPU_HUNG && hung_snaps == 1, "watch_hung_status_and_snapshot");
	CK(block_polls == 0 && charger_polls == 0 && ev_calls == 0, "watch_hung_no_services_after");
	watch_reset(5000ULL * clk_freq);
	charger_enabled = 1;
	uefi_list = 0;
	clk_now += 6 * clk_freq;
	live.heartbeat += 1;
	wr(&live, 5000ULL * clk_freq);
	CK(charger_polls == 0, "watch_charger_waits_for_uefi_list");
	uefi_list = 1;
	clk_now += 6 * clk_freq;
	live.heartbeat += 1;
	wr(&live, 5000ULL * clk_freq);
	CK(charger_polls == 1, "watch_charger_polls_for_uefi_list");
	charger_enabled = 0;
	kernel_resources_held = 0;
	usb_rx_queued = usb_out_owned = usb_in_owned = usb_out_lost = usb_in_lost = 0;
}
static void ret_reset(int64_t *states)
{
	psci_aff_idx = 0;
	for (int i = 0; i < 8; i++) {
		psci_aff[i] = states[i];
	}
	takebacks_n = held_irq_masked = 0;
}
static uint64_t cpus[8] = { 0x81000000ULL, 0x80000100ULL, 0x80000200ULL, 0 };
static void sc_return(void)
{
	int64_t all_off[8] = { 1, 1, 1, 1, 1, 1, 1, 1 };
	static const int64_t bads[] = { 0, 2, -1, 5 };
	kernel_resources_held = 1;
	ret_reset(all_off);
	lc_after_return(1, cpus, 3);
	CK(kernel_resources_held == 0, "ret_other_off_releases");
	CK(takebacks_n == 1 && takebacks[0] && !strcmp(takebacks[0], "the kernel came back"), "ret_other_off_takeback");
	for (unsigned b = 0; b < 4; b++) {
		int64_t st[8] = { 1, 1, 1, 1, 1, 1, 1, 1 };
		st[2] = bads[b];
		kernel_resources_held = 1;
		cap_reset();
		ret_reset(st);
		lc_after_return(1, cpus, 3);
		CK(kernel_resources_held == 1, "ret_other_notoff_holds");
		CK(takebacks_n == 0, "ret_other_notoff_no_takeback");
		CK(cap_has("the firmware answers"), "ret_prints_state");
	}
	{
		int64_t st[8] = { 1, 1, -1, 1, 1, 1, 1, 1 };
		cap_reset();
		kernel_resources_held = 1;
		ret_reset(st);
		lc_after_return(1, cpus, 3);
		CK(cap_has("answers -1"), "ret_prints_neg1");
	}
	kernel_resources_held = 0;
	ret_reset(all_off);
	lc_after_return(0, cpus, 3);
	CK(kernel_resources_held == 0, "ret_same_off_free");
	CK(takebacks_n == 1, "ret_same_off_takeback");
	for (unsigned b = 0; b < 4; b++) {
		int64_t st[8] = { 1, 1, 1, 1, 1, 1, 1, 1 };
		st[1] = bads[b];
		kernel_resources_held = 0;
		ret_reset(st);
		lc_after_return(0, cpus, 3);
		CK(kernel_resources_held == 1, "same_notoff_holds");
		CK(takebacks_n == 0, "same_notoff_no_takeback");
		CK(lc_cmd_denied(kernel_resources_held, 0, 0, "download:1f000"), "same_notoff_download_denied");
	}
}
static void sc_collect(void)
{
	static uint64_t rec[2], snd[2], junk[2];
	static struct kernel_live live;
	rec[0] = (uint64_t)(uintptr_t)"screen data"; rec[1] = 1000;
	snd[0] = (uint64_t)(uintptr_t)"sound data"; snd[1] = 900; memset(&live, 0, sizeof(live));
	live.magic2 = KERNEL_LIVE_MAGIC2; live.recording = (uint64_t)(uintptr_t)junk - 0x100000;
	live.sound = (uint64_t)(uintptr_t)snd - 0x100000;
	kernel_resources_held = 1;
	lc_after_return_collect(&live, 0x100000, 0);
	CK(recording_data == NULL && sound_data == NULL, "collect_held_skips_kernel_memory");
	kernel_resources_held = 0;
	junk[0] = 0; junk[1] = 9;
	lc_after_return_collect(&live, 0x100000, 0);
	CK(recording_data == NULL, "collect_bounds_header");
	junk[0] = (uint64_t)(uintptr_t)rec;
	junk[1] = 1000;
	lc_after_return_collect(&live, 0x100000, 0);
	CK(recording_data != NULL && recording_len == 1000, "collect_reads_when_free");
	lc_after_return_collect(NULL, 0, 0);
	CK(recording_data == NULL && sound_data == NULL, "collect_no_live");
}
static void sc_write_logs(void)
{
	kernel_resources_held = 1;
	active_watch = firmware_fault = 0;
	bio_present = 1;
	find_calls = w_blocks = flushes = compose_calls = 0;
	cap_reset();
	write_logs("k", 1);
	CK(compose_calls == 1, "wlog_held_stages");
	CK(find_calls == 0 && w_blocks == 0 && flushes == 0, "wlog_held_no_firmware_calls");
	CK(cap_has("KERNEL LOG"), "wlog_held_echoes");
	active_watch = 1;   /* the watch loop keeps its cooperative flush (redwood notes) */
	find_calls = w_blocks = 0;
	write_logs("k", 1);
	CK(find_calls == 1 && w_blocks > 0, "wlog_watch_allowed");
	active_watch = 0;
	kernel_resources_held = 0;
	find_calls = w_blocks = flushes = 0;
	cap_reset();
	write_logs("k", 1);
	CK(find_calls == 1 && w_blocks > 0 && flushes == 1, "wlog_free_writes");
	CK(cap_has("read back the same"), "wlog_free_readback");
	bio_present = 0;
	find_calls = 0;
	write_logs("k", 1);
	CK(find_calls == 1 && cap_has("END OF LOG"), "wlog_nobio_echo");
	kernel_resources_held = 1; psci_next = 0;
	psci_call(0x84000009u, 0, 0, 0);   /* the reset call does not clear the hold on its own */
	CK(kernel_resources_held == 1, "reset_keeps_hold");
	find_calls = w_blocks = 0;
	write_logs("k", 1);
	CK(find_calls == 0 && w_blocks == 0, "reset_hold_still_gated");
	kernel_resources_held = 0;
}
static void sc_exception(void)
{
	static uint64_t ctxb[128];
	/* annibale quiet screen: the handler never returns from its poll loop; the exemption
	 * must already be revoked when that loop starts serving commands */
	USB = &h_usb; usb_connected = 1;
	usb_rx_queued = usb_out_owned = usb_in_owned = usb_out_lost = usb_in_lost = 0;
	ev_step = ev_change = 0;
	PLAT = &platform_annibale; screen_quiet = 1;
	active_watch = kernel_resources_held = 1;
	firmware_fault = 0;
	running_live = (const struct kernel_live *)0x1000;
	find_calls = w_blocks = fb_calls = irq_mask_calls = 0;
	fb_armed = 1;
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	strcpy((char *)rx_store, "x");
	if (setjmp(jb_exit) == 0) {
		exception_handler(11, ctxb);
		CK0("exc_annibale_returned");
	}
	fb_armed = 0;
	CK(fb_calls == 1, "exc_poll_serves_commands");
	CK(irq_mask_calls == 1, "exc_masks_interrupts");
	CK(active_watch == 0, "exc_revokes_exemption");
	CK(firmware_fault == 1, "exc_marks_fault");
	CK(running_live == NULL, "exc_drops_log_pointer");
	CK(!strncmp(last_result, "firmware-fault", 14), "exc_records_result");
	CK(find_calls == 0 && w_blocks == 0, "exc_no_storage_after_fault");
	/* the other platform: the flush runs, the fault gate keeps it off the partition */
	PLAT = &plat_two;
	active_watch = 1;
	find_calls = 0;
	res_calls = 0;
	if (setjmp(jb_exit) == 0) {
		exception_handler(11, ctxb);
		CK0("exc_reset_returned");
	}
	CK(res_calls == 1, "exc_resets_after_flush");
	CK(find_calls == 0 && active_watch == 0, "exc_flush_gated");
	kernel_resources_held = 0;
	firmware_fault = 0;
}
static void usb_clean(void)
{
	USB = &h_usb; usb_connected = kernel_resources_held = 1;
	firmware_fault = 0;
	running_live = NULL;
	usb_rx_queued = usb_out_owned = usb_in_owned = usb_out_lost = usb_in_lost = 0;
	ev_calls = ev_sends = ev_after = ev_change = ev_change_state = 0;
	ev_msg = UsbDeviceEventNoEvent; ev_step = clk_freq / 10; clk_now = 0;
	ev_err = 0; ev_status = -1; ev_size = 0; ev_size_set = 0; ev_no_events = 0; ev_buf = NULL;
	ev_out_buf = ev_in_buf = NULL;
	usb_out_buf = NULL; usb_out_len = 0; usb_in_buf = NULL; usb_in_len = 0;
}
static void sc_usb(void)
{
	usb_clean();
	int64_t r = usb_wait(USB_EP_IN);
	CK(r == -1, "usb_held_bounded_exit");
	CK(ev_calls <= 60 && ev_calls > 20, "usb_held_bounded_time");
	CK(pets >= 2, "usb_held_feeds_watchdog");
	/* nothing held and no fault: waiting for the next command is the job, no timeout */
	kernel_resources_held = 0;
	ev_calls = 0; ev_after = 80; ev_step = 1;
	r = usb_wait(USB_EP_OUT);
	CK(r == -1 && ev_calls == 81, "usb_idle_keeps_waiting");
	/* a verified completion for the submitted request answers with its length; a
	 * notification for another endpoint does not end the wait */
	ev_calls = ev_after = 0;
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 512;
	r = usb_tx((const uint8_t *)"AB", 512);
	CK(r == 512, "usb_transfer_complete");
	ev_ep = USB_EP_OUT; ev_after = 7; ev_calls = 0;
	r = usb_tx((const uint8_t *)"CD", 512);
	CK(r == -1 && ev_calls == 8 && usb_in_lost == 1, "usb_other_endpoint_ignored");
}
static void sc_usb_owner(void)
{
	/* a held IN transfer that times out stops: no resend, no write over the busy buffer */
	usb_clean();
	memcpy(tx_store, "AA", 2);
	int64_t r = usb_tx((const uint8_t *)"AB", 2);
	CK(r == -1 && usb_in_lost == 1, "usb_tx_timeout_stops");
	tx_store[0] = 'Z';   /* sentinel: a stopped channel must not overwrite the owned buffer */
	r = usb_tx((const uint8_t *)"CD", 2);
	CK(r == -1 && ev_sends == 1 && tx_store[0] == 'Z', "usb_tx_lost_no_resend_no_overwrite");
	/* an OUT receive that times out hands the buffer back to the queue rule */
	usb_clean();
	r = usb_rx(rx_store, 64);
	CK(r == -1 && usb_out_lost == 0 && usb_rx_queued == 1 && usb_out_owned == 1, "usb_rx_timeout_keeps_queue");
	ev_sends = 0; poll_once();
	CK(ev_sends == 0, "usb_poll_no_resend_while_queued");
	/* the late confirmation clears ownership and the command is processed normally */
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	strcpy((char *)rx_store, "y"); fb_calls = 0; poll_once();
	CK(fb_calls == 1 && usb_out_owned == 0 && usb_rx_queued == 0, "usb_late_confirmation_recovers");
	/* a completion on the other endpoint says nothing about the one being waited on */
	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_after = 5;
	r = usb_rx(rx_store, 64);
	CK(r == -1 && usb_out_owned == 1, "usb_other_endpoint_not_confirmed");
	/* a device change cannot confirm an end: the channel stops and keeps its buffer */
	ev_msg = UsbDeviceEventNoEvent; ev_after = 0; ev_change = 1;
	r = usb_wait(USB_EP_OUT);
	CK(r == -1 && usb_out_lost == 1, "usb_device_change_stops");
	ev_change = 0;
	strcpy((char *)rx_store, "keep"); ev_sends = 0;
	r = usb_rx(rx_store, 64);
	CK(r == -1 && rx_store[0] == 'k' && ev_sends == 0, "usb_rx_lost_keeps_buffer");
	/* the cable comes back: the link is up, the stopped channel stays stopped */
	ev_change = 1; ev_change_state = UsbDeviceStateConnected;
	r = usb_wait(USB_EP_OUT);
	CK(r == -1 && usb_connected == 1 && usb_out_lost == 1 && usb_rx_queued == 0, "usb_relink_still_lost");
	ev_change = 0; ev_sends = 0; poll_once();
	CK(ev_sends == 0, "usb_poll_lost_no_resend");
	/* idle with nothing owned: a device change stops nothing */
	usb_clean(); kernel_resources_held = 0; ev_change = 1;
	r = usb_wait(USB_EP_OUT);
	CK(r == -1 && usb_out_lost == 0 && usb_out_owned == 0, "usb_idle_change_stops_nothing");
}
static void sc_fault(void)
{
	kernel_resources_held = active_watch = 0; firmware_fault = 1;
	bio_present = 1;
	find_calls = w_blocks = 0; cap_reset();
	write_logs("k", 1);
	CK(find_calls == 0 && w_blocks == 0 && cap_has("KERNEL LOG"), "wlog_fault_echo_only");
	usb_clean(); kernel_resources_held = 0; firmware_fault = 1;
	CK(usb_wait(USB_EP_OUT) == -1 && ev_calls <= 60, "usb_fault_bounded");
	firmware_fault = 0;
}
static void sc_repeat(void)
{
	for (int i = 0; i < 1000; i++) {
		one_issue(-4, 1, "repeat_stable");
		if (!lc_cmd_denied(kernel_resources_held, 0, 0, "oem run") || lc_stop_action(kernel_resources_held, 0) != LC_STOP_HELD) {
			CK0("repeat_stable");
			break;
		}
	}
}
/* fb_reply packs into its own stack buffer: usb_tx stays the only writer of the shared one */
static void sc_fb(void)
{
	static char long_text[201];

	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 8;
	fb_reply("OKAY", "base");
	CK(!usb_in_owned && !usb_in_lost && ev_sends == 1, "fb_healthy_first");
	CK(memcmp(tx_store, "OKAYbase", 8) == 0, "fb_healthy_first_bytes");
	fb_reply("OKAY", "next");
	CK(!usb_in_owned && !usb_in_lost && ev_sends == 2, "fb_healthy_second");
	CK(memcmp(tx_store, "OKAYnext", 8) == 0, "fb_healthy_second_bytes");
	/* the counterexample: the sentinel bytes stand before the IN transfer is submitted; after
	 * the timeout made the transfer owned and lost, neither reply form may write over them */
	usb_clean();
	memset(tx_store, 0xa5, sizeof(tx_store));
	fb_reply("OKAY", "base");   /* one send, no confirmation: owned and lost from here on */
	CK(usb_in_owned == 1 && usb_in_lost == 1 && ev_sends == 1, "fb_first_timeout");
	uint8_t saved[128];
	memcpy(saved, tx_store, sizeof(saved));
	fb_reply_data(0x1234);
	CK(memcmp(saved, tx_store, sizeof(saved)) == 0 && ev_sends == 1, "fb_lost_data_preserves");
	fb_reply("FAIL", "next");
	CK(memcmp(saved, tx_store, sizeof(saved)) == 0, "fb_lost_reply_preserves");
	CK(ev_sends == 1, "fb_lost_no_resend");
	/* hard limit: a long text never packs past the local buffer (code 4 + text up to 63) */
	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 63;
	memset(long_text, 't', sizeof(long_text) - 1);
	memset(tx_store, 0xa5, 128);
	fb_reply("OKAY", long_text);
	CK(ev_sends == 1 && tx_store[0] == 'O' && tx_store[62] == 't' && tx_store[63] == 0xa5, "fb_hard_cap");
}

/* notification verification (rev5): only a CompleteOK matching the recorded request ends one;
 * Active, unknown values, wrong buffers, oversized counts, short events and failed calls keep
 * or stop exactly as the ownership comment says, and a completion at the deadline is kept */
static void sc_usb_notify(void)
{
	int64_t r;

	/* Active on the queued receive: ownership kept, nothing resent, nothing dispatched */
	usb_clean();
	poll_once();
	CK(usb_out_owned == 1 && usb_rx_queued == 1 && ev_sends == 1, "usb_notify_queued");
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	ev_status = UsbDeviceTransferStatusActive;
	strcpy((char *)rx_store, "a"); fb_calls = 0; poll_once();
	CK(usb_out_owned == 1 && usb_rx_queued == 1 && usb_out_lost == 0, "usb_notify_active_keeps");
	CK(fb_calls == 0, "usb_notify_active_no_dispatch");
	ev_msg = UsbDeviceEventNoEvent; poll_once();
	CK(ev_sends == 1, "usb_notify_active_no_resend");
	/* a status outside the four documented values keeps the same way */
	ev_status = 7; ev_msg = UsbDeviceEventTransferNotification; poll_once();
	CK(usb_out_owned == 1 && usb_rx_queued == 1 && usb_out_lost == 0 && fb_calls == 0, "usb_notify_unknown_keeps");
	/* the single verified terminal notification ends the request and dispatches once */
	ev_status = -1; ev_bytes = 2; strcpy((char *)rx_store, "x"); poll_once();
	CK(usb_out_owned == 0 && usb_rx_queued == 0 && fb_calls == 1, "usb_notify_complete_once");
	/* a notification naming the other endpoint leaves the queued request alone */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 2;
	poll_once();
	CK(usb_out_owned == 1 && usb_rx_queued == 1 && ev_sends == 1, "usb_notify_other_endpoint");
	/* a buffer the loader did not submit: stopped, unread, never resent */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	ev_buf = tx_store; fb_calls = 0; poll_once();
	CK(usb_out_lost == 1 && fb_calls == 0, "usb_notify_wrong_buffer");
	ev_msg = UsbDeviceEventNoEvent; ev_sends = 0; poll_once();
	CK(ev_sends == 0, "usb_notify_wrong_buffer_no_resend");
	/* more bytes completed than submitted, and an event too small to check: both stop */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 600;
	poll_once();
	CK(usb_out_lost == 1 && fb_calls == 0, "usb_notify_long_bytes");
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	ev_size_set = 1; ev_size = 8;
	poll_once();
	CK(usb_out_lost == 1 && fb_calls == 0, "usb_notify_short_size");
	/* a failed handle_event call is no event at all */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_err = 1;
	poll_once();
	CK(usb_out_owned == 1 && usb_rx_queued == 1 && usb_out_lost == 0, "usb_notify_handle_error");
	ev_err = 0; ev_msg = UsbDeviceEventNoEvent; ev_sends = 0; poll_once();
	CK(ev_sends == 0, "usb_notify_handle_error_no_resend");
	/* an IN request still Active when the held budget runs out times out owned, never released */
	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_status = UsbDeviceTransferStatusActive;
	r = usb_tx((const uint8_t *)"OKAYactv", 8);
	CK(r == -1 && usb_in_owned == 1 && usb_in_lost == 1 && ev_sends == 1 && ev_calls <= 60, "usb_notify_active_in_timeout");
	/* a completion arriving past the three-second mark is still taken, not thrown away */
	usb_clean();
	ev_step = clk_freq / 2; ev_no_events = 6;   /* the seventh call lands beyond the budget */
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 8;
	r = usb_tx((const uint8_t *)"OKAYtime", 8);
	CK(r == 8 && usb_in_owned == 0 && usb_in_lost == 0, "usb_notify_deadline_kept");
	/* the same budget still bounds a wait that sees no completion */
	usb_clean();
	CK(usb_wait(USB_EP_IN) == -1 && ev_calls <= 60, "usb_notify_deadline_bounded");
	/* a late IN notification that finds no request releases nothing and stops nothing */
	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 8;
	CK(usb_wait(USB_EP_IN) == -1 && usb_in_owned == 0 && usb_in_lost == 0, "usb_notify_late_duplicate");
}

/* guards (rev6): a transfer event too short to read stops every owned request whichever value
 * its unreadable endpoint byte holds; a full DeviceStateChange below the state field leaves the
 * cable flag alone; an event after a stop changes nothing; a queued request is taken over only
 * by the caller holding its recorded buffer and length */
static void sc_usb_guard(void)
{
	int64_t r;

	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 2;
	ev_size_set = 1; ev_size = 3; fb_calls = 0; poll_once();
	CK(usb_out_lost == 1 && usb_out_owned == 1 && fb_calls == 0, "usb_guard_short_ep_in");
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	ev_size_set = 1; ev_size = 1; fb_calls = 0; poll_once();
	CK(usb_out_lost == 1 && usb_out_owned == 1 && fb_calls == 0, "usb_guard_short_ep_out");
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_bytes = 2;
	ev_size_set = 1; ev_size = 0; fb_calls = 0; poll_once();
	CK(usb_out_lost == 1 && usb_out_owned == 1 && fb_calls == 0, "usb_guard_short_size_zero");
	/* the same short event seen by a waiting IN request stops it too, endpoint byte aside */
	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	ev_size_set = 1; ev_size = 2;
	r = usb_tx((const uint8_t *)"OKAYshrt", 8);
	CK(r == -1 && usb_in_lost == 1 && usb_in_owned == 1 && ev_sends == 1, "usb_guard_short_in_wait");
	/* a DeviceStateChange too short to carry its state leaves the cable flag as it was */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventDeviceStateChange; ev_size_set = 1; ev_size = 2;
	CK(usb_wait(USB_EP_OUT) == -1 && usb_connected == 1 && usb_out_lost == 1, "usb_guard_short_state_kept");
	/* after a stop the matching CompleteOK dispatches nothing and releases nothing */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 2;
	ev_buf = tx_store; fb_calls = 0; poll_once();
	CK(usb_out_lost == 1 && usb_out_owned == 1 && usb_rx_queued == 1 && fb_calls == 0, "usb_guard_buf_stops");
	ev_buf = NULL; poll_once();
	CK(fb_calls == 0 && usb_out_owned == 1 && usb_rx_queued == 1 && usb_out_lost == 1, "usb_guard_lost_ignores_late_complete");
	ev_msg = UsbDeviceEventNoEvent; ev_sends = 0; poll_once();
	CK(ev_sends == 0, "usb_guard_lost_no_resend");
	/* an IN wait after a stop ignores even a matching CompleteOK and stays stopped */
	usb_clean();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_IN; ev_status = UsbDeviceTransferStatusCancelled;
	r = usb_tx((const uint8_t *)"OKAYcanc", 8);
	CK(r == -1 && usb_in_lost == 1 && usb_in_owned == 1, "usb_guard_cancel_stops");
	ev_status = -1;
	CK(usb_wait(USB_EP_IN) == -1 && usb_in_owned == 1 && usb_in_lost == 1 && ev_calls <= 60, "usb_guard_in_lost_ignores_late");
	/* foreign parameters may not take over the queued request: refused, record and ownership kept */
	usb_clean(); poll_once();
	r = usb_rx(rx_store, 64);
	CK(r == -1 && usb_rx_queued == 1 && usb_out_owned == 1 && usb_out_lost == 0 && ev_sends == 1, "usb_guard_reuse_refused");
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 300;
	strcpy((char *)rx_store, "long"); fb_calls = 0; poll_once();
	CK(fb_calls == 1 && usb_out_lost == 0 && usb_rx_queued == 0, "usb_guard_reuse_record_kept");
	/* the caller holding the recorded buffer and length takes the queued request over */
	usb_clean(); poll_once();
	ev_msg = UsbDeviceEventTransferNotification; ev_ep = USB_EP_OUT; ev_bytes = 4;
	strcpy((char *)rx_store, "take");
	r = usb_rx(rx_store, 511);
	CK(r == 4 && usb_rx_queued == 0 && usb_out_owned == 0 && ev_sends == 1, "usb_guard_reuse_takeover");
}

int main(void)
{
	sc_psci_spec(); sc_cpu_on(); sc_commands(); sc_enter(); sc_watch(); sc_return(); sc_collect();
	sc_write_logs(); sc_exception(); sc_usb(); sc_usb_owner(); sc_fb(); sc_usb_notify(); sc_usb_guard(); sc_fault(); sc_repeat();
	if (fail_count == 0) { printf("production logic: lifecycle checks passed\n"); } else { printf("%d failures\n", fail_count); }
	return fail_count > 127 ? 127 : fail_count;
}

"""
