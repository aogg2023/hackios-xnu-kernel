/*
 * adreno8_hfi.c -- the messages: the queues, the performance and bandwidth tables, the 3D core's firmware start and power: steps 12 to 16
 * The ladder, the boot-args and what is not settled: see adreno8.c; the shared world: adreno8_private.h.
 */
#include "adreno8_private.h"

#define printf adreno8_say

/* ---- the messages ----------------------------------------------------------------------------------------- */

/*
 * The two queues of the table made in step 9, talked to exactly as Linux's a6xx_hfi.c talks to
 * them: a message is a header dword -- id, size in dwords, and a 12-bit number that the answer
 * must carry back -- then its words; it goes into the command queue's ring (padded to four, the
 * padding 0xfafafafa so the firmware's own walks notice it), the doorbell word is rung, and the
 * response queue is read until the number matches. An error message for something else is printed
 * and skipped, as Linux skips it: it was not an answer to us.
 */

static unsigned adreno8_hfi_seq;

static uint32_t *
adreno8_queue_header(unsigned response)
{
	return (uint32_t *)(adreno8_gmu_hfi.memory + (response ? 72u : 24u) * 4);
}

static bool
adreno8_queue_write(const uint32_t *msg, uint32_t dwords)
{
	uint32_t *h = adreno8_queue_header(0), *data = (uint32_t *)(adreno8_gmu_hfi.memory + PAGE_SIZE);
	uint32_t size = h[3], index = h[11], space;

	space = index >= h[10] ? size - (index - h[10]) : h[10] - index;
	if (space < dwords) {
		h[5]++;
		adreno8_to_memory(adreno8_gmu_hfi.memory, PAGE_SIZE);
		printf("adreno8: %u: only %u of the command queue's %u words are free: dropped, the count says %u now\n", adreno8_step,
		    space, size, h[5]);
		return false;
	}
	for (uint32_t i = 0; i < dwords; i++) {
		data[index] = msg[i];
		index = (index + 1) % size;
	}
	for (; index % 4; index = (index + 1) % size) {
		data[index] = 0xfafafafa;
	}
	h[11] = index;
	adreno8_to_memory(adreno8_gmu_hfi.memory, 2 * PAGE_SIZE);
	adreno8_wr(adreno8_gmu.base + GMU_HOST2GMU_SET, 1);
	return true;
}

bool
adreno8_hfi_send(uint32_t id, const uint32_t *payload, uint32_t count, const char *what)
{
	uint32_t msg[176], resp[11], *h, size, index, hdr;

	if (count + 1 > sizeof(msg) / sizeof(msg[0])) {
		printf("adreno8: %u: %u is too many words for this stepper's one message\n", adreno8_step, count + 1);
		return false;
	}
	adreno8_hfi_seq = (adreno8_hfi_seq + 1) % 0xfff;
	msg[0] = ((uint32_t)adreno8_hfi_seq << 20) | ((count + 1) << 8) | id;
	for (uint32_t i = 0; i < count; i++) {
		msg[i + 1] = payload[i];
	}
	if (!adreno8_queue_write(msg, count + 1)) {
		printf("adreno8: %u: the message %u (%s) could not be put into the queue\n", adreno8_step, id, what);
		return false;
	}
	for (;;) {
		if (!adreno8_wait_for(&adreno8_gmu, GMU_GMU2HOST_INFO, 1, 1, 1000)) {
			printf("adreno8: %u: message %u (%s) number %u got no answer in a second\n", adreno8_step, id, what, adreno8_hfi_seq);
			adreno8_gmu_post_mortem();
			return false;
		}
		adreno8_wr(adreno8_gmu.base + GMU_GMU2HOST_CLR, 1);           /* "I have read it" */
		for (;;) {
			h = adreno8_queue_header(1);
			size = h[3];
			if (h[10] == h[11]) {
				break;                          /* empty: maybe an answer to an older message is still coming */
			}
			hdr = adreno8_rd(adreno8_hfi_view_resp + 4 * (h[10] % size));
			for (uint32_t i = 0; i < 11; i++) {
				resp[i] = adreno8_rd(adreno8_hfi_view_resp + 4 * ((h[10] + i) % size));
			}
			index = (hdr >> 8 & 0xff) + h[10];
			h[10] = ((index + 3) & ~3u) % size;   /* Linux aligns the read index too */
			adreno8_to_memory(adreno8_gmu_hfi.memory, PAGE_SIZE);
			if ((hdr & 0xff) == 100) {
				printf("adreno8: %u: the GMU's firmware complains on its own: error %u\n", adreno8_step, resp[1]);
				continue;
			}
			if ((resp[1] >> 20 & 0xfff) != adreno8_hfi_seq) {
				printf("adreno8: %u: an answer to message number %u came by; we are waiting for %u: reading on\n", adreno8_step,
				    resp[1] >> 20 & 0xfff, adreno8_hfi_seq);
				continue;
			}
			if (resp[2] != 0) {
				printf("adreno8: %u: message %u (%s) came back refusing: error %u\n", adreno8_step, id, what, resp[2]);
				return false;
			}
			return true;
		}
	}
}

/* ---- step 11: the firmware's processor out of reset -------------------------------------------------------- */

/*
 * The order of words Linux writes before it lets the processor go (a6xx_gmu_fw_start's a8xx
 * branch plus the gbif writes of its power up), then a6xx_gmu_start itself. The chip word is the
 * catalog's for gen80200; the log word is the log block's address with the page count minus one
 * in its low byte; the fence range 0 is Linux's own value for a8xx. After the last write, the
 * firmware's result word must show its started mark within a second (Linux gives it ten).
 */

/* ---- step 12: the queues ----------------------------------------------------------------------------------- */

bool
adreno8_step_queues(void)
{
	if (!adreno8_cx_ready()) {
		return false;
	}
	if (!adreno8_gmu_runs) {
		printf("adreno8: %u: no firmware running to talk to: step 11\n", adreno8_step);
		return false;
	}
	adreno8_announce("ring for the queues to come up, and wait for the status word's bit (Linux: a6xx_hfi_ctrl_init, 500 ms like the vendor's)");
	printf("adreno8: %u: what the queues' table says now, read without cache: version %#x; command queue status %u index %u/%u, response queue status %u index %u/%u\n",
	    adreno8_step, adreno8_rd(adreno8_hfi_view_table), adreno8_rd(adreno8_hfi_view_table + 24 * 4), adreno8_rd(adreno8_hfi_view_table + (24 + 10) * 4),
	    adreno8_rd(adreno8_hfi_view_table + (24 + 11) * 4), adreno8_rd(adreno8_hfi_view_table + 72 * 4), adreno8_rd(adreno8_hfi_view_table + (72 + 10) * 4),
	    adreno8_rd(adreno8_hfi_view_table + (72 + 11) * 4));
	adreno8_put(&adreno8_gmu, "gmu2host clr", GMU_GMU2HOST_CLR, ~0u);
	adreno8_put(&adreno8_gmu, "gmu2host mask: quiet about messages (we poll them), loud about the firmware faulting", GMU_GMU2HOST_MASK,
	    ~(1u << 23));
	adreno8_put(&adreno8_gmu, "hfi ctrl init", GMU_HFI_CTRL_INIT, 1);
	if (!adreno8_wait_for(&adreno8_gmu, GMU_HFI_CTRL_STATUS, 1, 1, 500)) {
		printf("adreno8: %u: half a second and the queues' status word is still %#x\n", adreno8_step,
		    adreno8_rd(adreno8_gmu.base + GMU_HFI_CTRL_STATUS));
		adreno8_gmu_post_mortem();
		return false;
	}
	adreno8_get(&adreno8_gmu, "hfi version", GMU_HFI_VERSION_INFO);
	adreno8_gmu_talks = true;
	printf("adreno8: %u: THE GMU'S MESSAGE QUEUES ARE UP\n", adreno8_step);
	return true;
}

/* ---- step 13: the speed and bandwidth tables ---------------------------------------------------------------- */

/*
 * What the messages carry (Linux: a6xx_gmu_rpmh_votes_init and a6xx_hfi_start's first two
 * messages). Every supply vote comes out of the RPMh static layout read in step 1; the bandwidth
 * rows are made the way a6xx_gmu_rpmh_bw_votes_init makes them, from the same layout, for the
 * static list of bandwidths. The table message is the a8xx one: the flat v1 message is what the
 * older chips get.
 */

static uint32_t adreno8_bw_votes[BW_COUNT][BCM_COUNT];

static bool
adreno8_fill_votes(void)
{
	for (unsigned i = 0; i < adreno8_gx_count; i++) {
		if (!adreno8_arc_vote("gfx.lvl", "gmxc.lvl", adreno8_gx_level[i], &adreno8_gx_votes[i]) ||
		    !adreno8_dep_vote(adreno8_gx_level[i], &adreno8_gx_dep_votes[i])) {
			return false;
		}
	}
	for (unsigned i = 0; i < 3; i++) {
		if (!adreno8_arc_vote("cx.lvl", "mx.lvl", adreno8_gmu_level[i], &adreno8_cx_votes[i])) {
			return false;
		}
	}
	printf("adreno8: %u: %u 3D-core speeds: %llu Hz votes %#x (depends %#x) ... %llu Hz votes %#x (depends %#x)\n", adreno8_step,
	    adreno8_gx_count, (uint64_t)adreno8_gx_hz[1], adreno8_gx_votes[1], adreno8_gx_dep_votes[1], (uint64_t)adreno8_gx_hz[adreno8_gx_count - 1], adreno8_gx_votes[adreno8_gx_count - 1],
	    adreno8_gx_dep_votes[adreno8_gx_count - 1]);
	printf("adreno8: %u: the GMU's own three speeds vote %#x %#x %#x\n", adreno8_step, adreno8_cx_votes[0], adreno8_cx_votes[1], adreno8_cx_votes[2]);
	return true;
}

/* The bandwidth rows (Linux: a6xx_gmu_rpmh_bw_votes_init, calculations "loosely copied from bcm_aggregate and tcs_cmd_gen"). */

static bool
adreno8_fill_bw_votes(void)
{
	const struct adreno8_bcm_db *aux[BCM_COUNT];
	unsigned length[BCM_COUNT], bcm_count = 0;

	for (unsigned i = 0; i < BCM_COUNT; i++) {
		const uint8_t *data = NULL;

		if (adreno8_rpmh_lookup(adreno8_bcms[i].name, NULL, NULL, &data, &length[i]) == 0 || length[i] < sizeof(struct adreno8_bcm_db)) {
			printf("adreno8: %u: no RPMh description for \"%s\": no bandwidth table\n", adreno8_step, adreno8_bcms[i].name);
			return false;
		}
		aux[i] = (const struct adreno8_bcm_db *)data;   /* little endian both sides */
		bcm_count++;
	}
	for (unsigned bw_index = 0; bw_index < BW_COUNT; bw_index++) {
		uint32_t bw = adreno8_bw_kb[bw_index];

		for (unsigned i = 0; i < bcm_count; i++) {
			bool commit = i == bcm_count - 1 ||
			    (aux[i + 1] != NULL && aux[i]->vcd != aux[i + 1]->vcd);
			uint64_t peak;
			uint32_t vote;

			if (bw == 0) {
				adreno8_bw_votes[bw_index][i] = BCM_TCS_CMD(commit, false, 0, 0);
				continue;
			}
			if (adreno8_bcms[i].fixed) {
				/* a8xx votes perfmode on every voted bandwidth (Linux's a7xx condition) */
				adreno8_bw_votes[bw_index][i] = BCM_TCS_CMD(commit, true, 0, adreno8_bcms[i].perfmode);
				continue;
			}
			peak = (uint64_t)bw * aux[i]->width / adreno8_bcms[i].buswidth;
			peak *= 1000;
			peak /= aux[i]->unit;
			vote = peak < 1 ? 1 : peak > 0x3fff ? 0x3fff : (uint32_t)peak;
			adreno8_bw_votes[bw_index][i] = BCM_TCS_CMD(commit, true, vote, vote);    /* a8xx votes both amounts */
		}
	}
	printf("adreno8: %u: %u bandwidth rows for %u monitors: %u KB/s votes %#x %#x %#x ... %u KB/s votes %#x %#x %#x\n",
	    adreno8_step, BW_COUNT, bcm_count, adreno8_bw_kb[0], adreno8_bw_votes[0][0], adreno8_bw_votes[0][1], adreno8_bw_votes[0][2], adreno8_bw_kb[BW_COUNT - 1],
	    adreno8_bw_votes[BW_COUNT - 1][0], adreno8_bw_votes[BW_COUNT - 1][1], adreno8_bw_votes[BW_COUNT - 1][2]);
	return true;
}

bool
adreno8_step_tables(void)
{
	uint32_t tbl[128], bw[159], at, n;
	uint32_t ddr_addr[BCM_COUNT];

	if (!adreno8_cx_ready()) {
		return false;
	}
	if (!adreno8_gmu_talks) {
		printf("adreno8: %u: nobody answers on the queues yet: step 12\n", adreno8_step);
		return false;
	}
	adreno8_announce("work out every supply vote from the RPMh layout of step 1, and hand the firmware the table of them (Linux: a8xx_hfi_send_perf_table: three words per 3D-core speed, two per GMU speed)");
	if (!adreno8_fill_votes()) {
		return false;
	}
	at = 0;
	tbl[at++] = 0;                        /* the table's version: v0, the only one */
	tbl[at++] = 1;                        /* HFI_TABLE_GPU_PERF */
	tbl[at++] = adreno8_gx_count;                 /* the 3D core's speeds */
	tbl[at++] = 3;                        /* words per one: the vote, the dependency, the kHz */
	for (unsigned i = 0; i < adreno8_gx_count; i++) {
		tbl[at++] = adreno8_gx_votes[i];
		tbl[at++] = adreno8_gx_dep_votes[i];
		tbl[at++] = adreno8_gx_hz[i] / 1000;
	}
	tbl[at++] = 3;                        /* the GMU's own speeds */
	tbl[at++] = 2;
	for (unsigned i = 0; i < 3; i++) {
		tbl[at++] = adreno8_cx_votes[i];
		tbl[at++] = adreno8_gmu_hz[i] / 1000;
	}
	n = at;
	if (!adreno8_hfi_send(15, tbl, n, "here is how to vote for power at every speed")) {
		return false;
	}
	printf("adreno8: %u: the speed table is in (%u words)\n", adreno8_step, n);

	adreno8_announce("and the bandwidth table: %u rows of the three memory monitors' RPMh words, the CN0 gate on and off, and the wait bits (Linux: a6xx_generate_bw_table, verbatim in shape)", BW_COUNT);
	if (!adreno8_fill_bw_votes()) {
		return false;
	}
	for (unsigned i = 0; i < BCM_COUNT; i++) {
		ddr_addr[i] = adreno8_rpmh_lookup(adreno8_bcms[i].name, NULL, NULL, NULL, NULL);
		if (ddr_addr[i] == 0) {
			printf("adreno8: %u: RPMh has no address for \"%s\"\n", adreno8_step, adreno8_bcms[i].name);
			return false;
		}
	}
	{
		uint32_t cn0 = adreno8_rpmh_lookup("CN0", NULL, NULL, NULL, NULL);

		if (cn0 == 0) {
			printf("adreno8: %u: RPMh has no \"CN0\": the firmware's memory gate is unknown\n", adreno8_step);
			return false;
		}
		memset(bw, 0, sizeof(bw));
		bw[0] = BW_COUNT;               /* bandwidth levels */
		bw[1] = 1;                      /* one CX monitor: CN0 */
		bw[2] = BCM_COUNT;              /* the three DDR-side ones */
		bw[3] = 1;                      /* wait for CN0's commit: row 0 has it */
		bw[4] = 0;
		for (unsigned j = 0; j < BCM_COUNT; j++) {
			if (adreno8_bw_votes[0][j] & (1u << 30)) {
				bw[4] |= 1u << j;   /* the commit bits of the first row, per Linux */
			}
		}
		bw[5] = cn0;                    /* CN0's address */
		bw[11] = BCM_TCS_CMD(true, false, 0, 0);      /* its off word */
		bw[17] = BCM_TCS_CMD(true, true, 0, 1u << 0); /* and its on word */
		for (unsigned j = 0; j < BCM_COUNT; j++) {
			bw[23 + j] = ddr_addr[j];
		}
		for (unsigned b = 0; b < BW_COUNT; b++) {
			for (unsigned j = 0; j < BCM_COUNT; j++) {
				bw[31 + b * 8 + j] = adreno8_bw_votes[b][j];
			}
		}
		if (!adreno8_hfi_send(3, bw, 159, "here is how to vote for bandwidth")) {
			return false;
		}
		printf("adreno8: %u: the bandwidth table is in\n", adreno8_step);
	}
	printf("adreno8: %u: THE FIRMWARE HAS ITS ORDERS: %u speeds, %u bandwidth levels\n", adreno8_step, adreno8_gx_count, BW_COUNT);
	return true;
}

/* ---- step 14: the core's own firmware ------------------------------------------------------------------------ */

bool
adreno8_step_core_fw(void)
{
	uint32_t feat[3], power;

	if (!adreno8_cx_ready()) {
		return false;
	}
	if (!adreno8_gmu_talks) {
		printf("adreno8: %u: the queues do not answer: step 12\n", adreno8_step);
		return false;
	}
	adreno8_announce("ask for the dozing-between-jobs power saving (Linux: a6xx_hfi_enable_ifpc: feature 9, long hysteresis 6000 ms). Where the firmware says no, we go on: it decides whether the core dozes, not whether it runs");
	feat[0] = 9;
	feat[1] = 1;
	feat[2] = 0x1680;
	if (!adreno8_hfi_send(11, feat, 3, "let the core doze between jobs")) {
		printf("adreno8: %u: (the firmware said no to dozing; going on)\n", adreno8_step);
	}
	adreno8_announce("and the message that starts the 3D core's own firmware (Linux: a6xx_hfi_send_core_fw_start: a header and nothing else)");
	if (!adreno8_hfi_send(14, NULL, 0, "start the core's firmware")) {
		return false;
	}
	power = adreno8_get(&adreno8_gmu, "power clock status (a look, no judgement: the core's firmware has only just learned this chip)",
	    GMU_PWR_CLK_STATUS);
	printf("adreno8: %u: the core's power as the GMU sees it at this moment: %s\n", adreno8_step, GMU_PWR_GX_UP(power) ? "up" : "off");
	adreno8_announce("and the word that starts its ordinary work (Linux sends it here; the vendor's driver sends it from its hw_init)");
	if (!adreno8_hfi_send(10, NULL, 0, "start working")) {
		return false;
	}
	printf("adreno8: %u: THE 3D CORE'S OWN FIRMWARE RUNS ALONGSIDE\n", adreno8_step);
	return true;
}

/* ---- step 15: the 3D core, fed and running ------------------------------------------------------------------- */

/*
 * The message that asks the GMU for a speed (Linux: a6xx_gmu_set_freq -> a6xx_hfi_set_freq:
 * blocking acknowledge, a speed index and a bandwidth index). The GMU owns the core's power on
 * this SoC -- gpucc has no HLOS switch for it -- so this message is the switch. Then two things
 * that only work with the core powered and clocked: the command processor's slice aperture, and
 * the counter that runs whenever the core has power and never stops while it has.
 */

bool
adreno8_step_gx_power(void)
{
	uint32_t freq_index, msg[3], power, ap, hi1, lo1, hi2, lo2;
	uint64_t start, waited = 0;

	if (!adreno8_cx_ready()) {
		return false;
	}
	if (!adreno8_gmu_talks) {
		printf("adreno8: %u: the queues do not answer: step 12\n", adreno8_step);
		return false;
	}
	freq_index = adreno8_speed_asked != 0 && adreno8_speed_asked < adreno8_gx_count ? adreno8_speed_asked : adreno8_gx_count - 1;
	adreno8_announce("ask the GMU for power and a speed for the 3D core (blocking), and watch its power word: on this SoC this message is the core's power switch");
	printf("adreno8: %u: speed %u of %u: %llu Hz%s\n", adreno8_step, freq_index, adreno8_gx_count - 1, (uint64_t)adreno8_gx_hz[freq_index],
	    adreno8_speed_asked == 0 ? " (the top one: adreno8_speed=N asks for another)" : "");
	msg[0] = 1;                   /* ack_type: blocking */
	msg[1] = freq_index;
	msg[2] = BW_COUNT - 1;        /* the top bandwidth, so no vote has to climb afterwards */
	if (!adreno8_hfi_send(30, msg, 3, "give the core power and a speed")) {
		return false;
	}
	start = mach_absolute_time();
	for (int i = 0; i < 20000 && !GMU_PWR_GX_UP(adreno8_rd(adreno8_gmu.base + GMU_PWR_CLK_STATUS)); i++) {
		adreno8_sleep_us(100);
	}
	absolutetime_to_nanoseconds(mach_absolute_time() - start, &waited);
	power = adreno8_get(&adreno8_gmu, "power clock status", GMU_PWR_CLK_STATUS);
	if (!GMU_PWR_GX_UP(power)) {
		printf("adreno8: %u: two seconds and the word still says %#x: supply %s, clock %s\n", adreno8_step, power,
		    power & 1 ? "off" : "on", power & 2 ? "off" : "on");
		adreno8_gmu_post_mortem();
		return false;
	}
	printf("adreno8: %u: THE 3D CORE HAS POWER AND A CLOCK: %llu us after the message the GMU had it up\n", adreno8_step,
	    waited / 1000);

	adreno8_announce("two things only the powered core can do: the command processor's aperture, told to use the slice ids and read back, and its always-on counter, read twice");
	ap = adreno8_get(&adreno8_gpu, "cp aperture, host", GPU_CP_APERTURE_HOST);
	adreno8_set(&adreno8_gpu, "cp aperture, host: bit 23 says use the slice ids", GPU_CP_APERTURE_HOST, ap | 1u << 23);
	adreno8_put(&adreno8_gpu, "cp aperture, host: back as it was", GPU_CP_APERTURE_HOST, ap);
	hi1 = adreno8_rd(adreno8_gpu.base + GPU_CP_ALWAYS_ON_HI);
	lo1 = adreno8_rd(adreno8_gpu.base + GPU_CP_ALWAYS_ON_LO);
	adreno8_sleep_ms(10);
	hi2 = adreno8_rd(adreno8_gpu.base + GPU_CP_ALWAYS_ON_HI);
	lo2 = adreno8_rd(adreno8_gpu.base + GPU_CP_ALWAYS_ON_LO);
	if (hi1 == hi2 && lo1 == lo2) {
		printf("adreno8: %u: NOTE: the core's counter stands still (%#x:%#x): powered, but no clock through the accumulator? the vendor's accumulator shift clocks were only tolerated back in step 5\n",
		    adreno8_step, hi2, lo2);
	} else {
		printf("adreno8: %u: the core's own counter moved: %#x:%#x to %#x:%#x in 10 ms\n", adreno8_step, hi1, lo1, hi2, lo2);
	}
	printf("adreno8: %u: THE 3D CORE IS FED: power, clock, and a counter that counts\n", adreno8_step);
	return true;
}

/* ---- step 16: the last look ------------------------------------------------------------------------------------ */

bool
adreno8_step_last_look(void)
{
	uint32_t ao, fence, slices, power;

	adreno8_announce("the last look around before handing off: the GMU's translation, its interrupts and fence, the slices, both counters, and the queue indices");
	if (adreno8_space_faulted(&adreno8_gmu_space)) {
		printf("adreno8: %u: NOTE: something was refused translation since we turned it on; the steps above had their answers anyway\n", adreno8_step);
	}
	ao = adreno8_get(&adreno8_gmu, "ao interrupt status", GMU_AO_INTERRUPT_STATUS);
	if (ao != 0) {
		printf("adreno8: %u: the GMU's urgent conditions: %#x%s%s%s\n", adreno8_step, ao, ao & 1 ? " [watchdog bit]" : "",
		    ao & 1u << 3 ? " [fence error]" : "", ao & 1u << 5 ? " [bus error]" : "");
	}
	fence = adreno8_get(&adreno8_gmu, "ahb fence status", GMU_AHB_FENCE_STATUS);
	if (fence != 0) {
		printf("adreno8: %u: NOTE: the fence has someone caught: %#x\n", adreno8_step, fence);
	}
	slices = adreno8_get(&adreno8_cx_misc, "slice enable final", CX_MISC_SLICE_ENABLE) & 7;
	slices = (slices & 1) + (slices >> 1 & 1) + (slices >> 2 & 1);
	if (slices != adreno8_slice_count) {
		printf("adreno8: %u: NOTE: the slices readable now (%u) are not what the fuses said in step 4 (%u)\n", adreno8_step, slices,
		    adreno8_slice_count);
	}
	power = adreno8_get(&adreno8_gmu, "power clock status", GMU_PWR_CLK_STATUS);
	adreno8_get(&adreno8_gmu, "always on counter, high", GMU_ALWAYS_ON_HI);
	adreno8_get(&adreno8_gmu, "always on counter, low", GMU_ALWAYS_ON_LO);
	if (adreno8_hfi_view_table != 0) {
		printf("adreno8: %u: the queues at the end: command %u/%u, response %u/%u, dropped %u\n", adreno8_step,
		    adreno8_rd(adreno8_hfi_view_table + (24 + 10) * 4), adreno8_rd(adreno8_hfi_view_table + (24 + 11) * 4),
		    adreno8_rd(adreno8_hfi_view_table + (72 + 10) * 4), adreno8_rd(adreno8_hfi_view_table + (72 + 11) * 4), adreno8_rd(adreno8_hfi_view_table + (24 + 5) * 4));
	}
	printf("adreno8: %u: ALL OF IT: the CX half awake, the GMU's firmware running (its log at %#x), the queues answering, the speed and bandwidth tables accepted, the 3D core powered, clocked, and counting with %u slice(s); the power word says %#x\n",
	    adreno8_step, adreno8_gmu_log.address, slices, power);
	return true;
}
