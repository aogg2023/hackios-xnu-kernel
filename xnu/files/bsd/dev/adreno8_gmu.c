/*
 * adreno8_gmu.c -- the GMU's own world: its memory, the firmware file and its load, the RSC wake and the start of its processor: steps 6, 7, 9, 10, 11, the post-mortem
 * The ladder, the boot-args and what is not settled: see adreno8.c; the shared world: adreno8_private.h.
 */
#include "adreno8_private.h"

#define printf adreno8_say

struct adreno8_block adreno8_gmu_ao_diag = { "gmu_ao_blk_dec0", 0x03d40000, 0x10000 };

/* ---- the GMU's own window ------------------------------------------------------------------------------- */

/*
 * dt: qcom,gmu@3d37000 (reg 0x3d37000 length 0x68000). The xml's numbers are dword indices in the
 * GPU block's space based at 0x3d00000, so the window's byte offset of dword n is 4n - 0x37000 --
 * Linux's GMU_BYTE_OFFSET with the vendor's own window base (Linux maps 0x3d2c000 and subtracts
 * 0x2c000; the phone's device tree says the firmware driver gets 0x3d37000 and 0x37000).
 */

struct adreno8_block adreno8_gmu = { "gmu", 0x03d37000, 0x68000 };

struct adreno8_block adreno8_gpu = { "gpu", 0x03d00000, 0x10000 };   /* the 3D core's front door and the gbif's CX side */

/* where the memory the firmware asks for lives: the addresses are the firmware's, in its space */

struct adreno8_gpu_block adreno8_gmu_demands[GMU_BLOCKS];

unsigned adreno8_gmu_demand_count;

struct adreno8_gpu_block adreno8_gmu_log, adreno8_gmu_hfi, adreno8_gmu_debug, adreno8_gmu_dummy;

bool adreno8_gmu_runs, adreno8_gmu_talks, adreno8_fw_loaded, adreno8_rsc_woken_by_us;

vm_offset_t adreno8_log_view[4];               /* the GMU's log, uncached: it writes while our caches guess */

vm_offset_t adreno8_hfi_view_table, adreno8_hfi_view_cmd, adreno8_hfi_view_resp;

bool
adreno8_block_new(struct adreno8_gpu_block *b, const char *name, uint32_t address, uint32_t size)
{
	if (size == 0 || size > GMU_BLOCK_CAP) {
		printf("adreno8: %u: \"%s\" asks for %#x bytes at %#x: not between 1 and %#x\n", adreno8_step, name, size, address,
		    GMU_BLOCK_CAP);
		return false;
	}
	if (adreno8_gmu_demand_count == GMU_BLOCKS) {
		printf("adreno8: %u: no room for another of the GMU's memory blocks\n", adreno8_step);
		return false;
	}
	if (kmem_alloc(kernel_map, (vm_offset_t *)&b->memory, (vm_size_t)((size + PAGE_MASK) & ~PAGE_MASK),
	    KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("adreno8: %u: no kernel memory for the GMU's %#x bytes\n", adreno8_step, size);
		b->memory = 0;
		return false;
	}
	b->name = name;
	b->address = address;
	b->size = size;
	adreno8_gmu_demand_count++;
	printf("adreno8: %u: memory for the GMU's %#x bytes at its %#x (%s): kernel %#llx; pages at %#llx", adreno8_step, size,
	    address, name, (uint64_t)b->memory, kvtophys(b->memory));
	for (uint32_t at = (size + PAGE_MASK) / PAGE_SIZE - 1; at > 0; at--) {
		printf(", %#llx", kvtophys(b->memory + at * PAGE_SIZE));
	}
	printf("\n");
	return true;
}

/* The writes of this processor have reached memory before the GMU reads it (redwood: settle). */

void
adreno8_settle(void)
{
	__asm__ volatile ("dsb sy");
}

/* Push what this processor has written to a stretch of memory out of its cache, and forget it: the GMU reads memory. */

void
adreno8_to_memory(vm_offset_t start, vm_size_t size)
{
	for (vm_offset_t line = start & ~(vm_offset_t)63; line < start + size; line += 64) {
		__asm__ volatile ("dc civac, %0" : : "r" (line));
	}
	adreno8_settle();
}

/* Whether the memory for this address was already made (a demand of the file, or one of ours). */

bool
adreno8_covered(uint32_t address, uint32_t size)
{
	const struct adreno8_gpu_block *fixed[4] = { &adreno8_gmu_log, &adreno8_gmu_hfi, &adreno8_gmu_debug, &adreno8_gmu_dummy };

	for (unsigned i = 0; i < adreno8_gmu_demand_count; i++) {
		if (address >= adreno8_gmu_demands[i].address && address + size <= adreno8_gmu_demands[i].address + adreno8_gmu_demands[i].size) {
			return true;
		}
	}
	for (unsigned i = 0; i < 4; i++) {
		if (fixed[i]->memory != 0 && address >= fixed[i]->address &&
		    address + size <= fixed[i]->address + fixed[i]->size) {
			return true;
		}
	}
	return false;
}

/* The kernel address of the GMU's address, or nothing. */

vm_offset_t
adreno8_seen_at(uint32_t address)
{
	const struct adreno8_gpu_block *fixed[4] = { &adreno8_gmu_log, &adreno8_gmu_hfi, &adreno8_gmu_debug, &adreno8_gmu_dummy };

	for (unsigned i = 0; i < adreno8_gmu_demand_count; i++) {
		if (address >= adreno8_gmu_demands[i].address && address < adreno8_gmu_demands[i].address + adreno8_gmu_demands[i].size) {
			return adreno8_gmu_demands[i].memory + (address - adreno8_gmu_demands[i].address);
		}
	}
	for (unsigned i = 0; i < 4; i++) {
		if (fixed[i]->memory != 0 && address >= fixed[i]->address &&
		    address < fixed[i]->address + fixed[i]->size) {
			return fixed[i]->memory + (address - fixed[i]->address);
		}
	}
	return 0;
}

/* What the GMU and its firmware say about themselves, printed when something of theirs failed. */

void
adreno8_gmu_post_mortem(void)
{
	uint32_t wptr, entries;

	if (adreno8_gmu.base == 0) {
		return;
	}
	printf("adreno8: %u: what the GMU says now: init result %#x, hfi %#x, gmu2host info %#x mask %#x, ao interrupts %#x, fence %#x, power/clk %#x\n",
	    adreno8_step, adreno8_rd(adreno8_gmu.base + GMU_FW_INIT_RESULT), adreno8_rd(adreno8_gmu.base + GMU_HFI_CTRL_STATUS), adreno8_rd(adreno8_gmu.base + GMU_GMU2HOST_INFO),
	    adreno8_rd(adreno8_gmu.base + GMU_GMU2HOST_MASK), adreno8_rd(adreno8_gmu.base + GMU_AO_INTERRUPT_STATUS), adreno8_rd(adreno8_gmu.base + GMU_AHB_FENCE_STATUS),
	    adreno8_rd(adreno8_gmu.base + GMU_PWR_CLK_STATUS));
	printf("adreno8: %u: ... and the RSC: status %#x seq busy %#x; the queue headers: command %#x/%#x response %#x/%#x (status, dropped)\n",
	    adreno8_step, adreno8_rd(adreno8_rscc.base + RSCC_RSC_STATUS), adreno8_rd(adreno8_rscc.base + RSCC_SEQ_BUSY),
	    adreno8_hfi_view_table != 0 ? adreno8_rd(adreno8_hfi_view_table + 24) : 0,
	    adreno8_hfi_view_table != 0 ? adreno8_rd(adreno8_hfi_view_table + 24 + 20) : 0,
	    adreno8_hfi_view_table != 0 ? adreno8_rd(adreno8_hfi_view_table + 72) : 0,
	    adreno8_hfi_view_table != 0 ? adreno8_rd(adreno8_hfi_view_table + 72 + 20) : 0);
	if (adreno8_log_view[0] == 0 || adreno8_gmu.base == 0) {
		return;
	}
	wptr = adreno8_rd(adreno8_gmu.base + GMU_GENERAL_9) & 0xffff;
	entries = wptr < 24 ? wptr : 24;
	printf("adreno8: %u: the GMU's log, last %u lines of %u it wrote:\n", adreno8_step, entries, wptr);
	for (uint32_t i = wptr - entries; i < wptr; i++) {
		uint32_t off = (i * 16) % 0x4000, page = off / 4096;

		if (adreno8_log_view[page] == 0) {
			continue;
		}
		printf("adreno8: %u:   log %u: %08x %08x %08x %08x\n", adreno8_step, i, adreno8_rd(adreno8_log_view[page] + off % 4096),
		    adreno8_rd(adreno8_log_view[page] + off % 4096 + 4), adreno8_rd(adreno8_log_view[page] + off % 4096 + 8), adreno8_rd(adreno8_log_view[page] + off % 4096 + 12));
	}
}

/* ---- step 6: the first words from the GMU ---------------------------------------------------------------- */

bool
adreno8_step_first_words(void)
{
	uint32_t power;

	if (!adreno8_cx_ready()) {
		return false;
	}
	adreno8_announce("read the GMU's whole front room at once, touching nothing: reset and boot words, its firmware's result word and the HFI queue state, the power and RPMh state, the interrupts old and new, the fence, its counters, and the three words the firmware is started with");
	adreno8_get(&adreno8_gmu, "cm3 sysreset", GMU_CM3_SYSRESET);
	adreno8_get(&adreno8_gmu, "cm3 boot config", GMU_CM3_BOOT_CONFIG);
	adreno8_get(&adreno8_gmu, "cm3 config", GMU_CM3_CFG);
	adreno8_get(&adreno8_gmu, "fw init result", GMU_FW_INIT_RESULT);
	adreno8_get(&adreno8_gmu, "hfi version", GMU_HFI_VERSION_INFO);
	adreno8_get(&adreno8_gmu, "hfi ctrl status", GMU_HFI_CTRL_STATUS);
	adreno8_get(&adreno8_gmu, "hfi qtbl info", GMU_HFI_QTBL_INFO);
	adreno8_get(&adreno8_gmu, "hfi qtbl address", GMU_HFI_QTBL_ADDR);
	power = adreno8_get(&adreno8_gmu, "power clock status", GMU_PWR_CLK_STATUS);
	if (power == 0xffffffff) {
		printf("adreno8: %u: the GMU does not answer at all: no clock, no power, or the hypervisor says no\n", adreno8_step);
		return false;
	}
	printf("adreno8: %u: the 3D core's power as the GMU sees it: %s\n", adreno8_step, GMU_PWR_GX_UP(power) ? "up" : "off");
	adreno8_get(&adreno8_gmu, "rpmh power state", GMU_RPMH_POWER_STATE);
	adreno8_get(&adreno8_gmu, "rpmh ctrl", GMU_RPMH_CTRL);
	adreno8_get(&adreno8_gmu, "fal intf", GMU_CX_FAL_INTF);
	adreno8_get(&adreno8_gmu, "falnext intf", GMU_CX_FALNEXT_INTF);
	adreno8_get(&adreno8_gmu, "gmu2host set", GMU_GMU2HOST_SET);
	adreno8_get(&adreno8_gmu, "gmu2host clr", GMU_GMU2HOST_CLR);
	adreno8_get(&adreno8_gmu, "gmu2host info", GMU_GMU2HOST_INFO);
	adreno8_get(&adreno8_gmu, "gmu2host mask", GMU_GMU2HOST_MASK);
	adreno8_get(&adreno8_gmu, "host2gmu set", GMU_HOST2GMU_SET);
	adreno8_get(&adreno8_gmu, "ao interrupt status", GMU_AO_INTERRUPT_STATUS);
	adreno8_get(&adreno8_gmu, "ao interrupt mask", GMU_AO_INTERRUPT_MASK);
	adreno8_get(&adreno8_gmu, "rscc control req", GMU_RSCC_CONTROL_REQ);
	adreno8_get(&adreno8_gmu, "rscc control ack", GMU_RSCC_CONTROL_ACK);
	adreno8_get(&adreno8_gmu, "ahb fence ctrl", GMU_AHB_FENCE_CTRL);
	adreno8_get(&adreno8_gmu, "ahb fence range 0", GMU_AHB_FENCE_RANGE_0);
	adreno8_get(&adreno8_gmu, "ahb fence range 1", GMU_AHB_FENCE_RANGE_1);
	adreno8_get(&adreno8_gmu, "ahb fence status", GMU_AHB_FENCE_STATUS);
	adreno8_get(&adreno8_gmu, "rbbm int unmasked", GMU_RBBM_INT_UNMASKED);
	adreno8_get(&adreno8_gmu, "general 8 (the log)", GMU_GENERAL_8);
	adreno8_get(&adreno8_gmu, "general 9 (log written)", GMU_GENERAL_9);
	adreno8_get(&adreno8_gmu, "general 10 (the chip)", GMU_GENERAL_10);
	adreno8_get(&adreno8_gmu, "always on counter, high", GMU_ALWAYS_ON_HI);
	adreno8_get(&adreno8_gmu, "always on counter, low", GMU_ALWAYS_ON_LO);
	if (adreno8_rd(adreno8_gmu.base + GMU_FW_INIT_RESULT) == GMU_FW_ALREADY) {
		printf("adreno8: %u: NOTE: the GMU's firmware is running already (its result word says so); steps 7 and 10 will refuse, and only a restart of the phone clears it\n", adreno8_step);
	}
	return true;
}

/* ---- step 7: its memory, in reset ------------------------------------------------------------------------ */

/*
 * Two words into the GMU's data memory and back, while its processor is in reset -- redwood's
 * step 10 unchanged but for the window. The word the firmware leaves at the end of that memory
 * says whether firmware is running, and a running firmware's memory is not ours to scribble on.
 */

bool
adreno8_step_gmu_memory(void)
{
	static const uint32_t first = 0xfeedfeee, second = 0xace5ba11;
	uint32_t save[2], result;

	if (!adreno8_cx_ready()) {
		return false;
	}
	result = adreno8_get(&adreno8_gmu, "fw init result", GMU_FW_INIT_RESULT);
	if (result == GMU_FW_ALREADY) {
		printf("adreno8: %u: the GMU's firmware is running: its data memory is its own; the phone's restart stops it\n", adreno8_step);
		return false;
	}
	adreno8_announce("write two words into the GMU's data memory (0x3d71000) and read them back: the way its firmware goes in");
	for (unsigned i = 0; i < 2; i++) {
		save[i] = adreno8_rd(adreno8_gmu.base + GMU_DTCM + 4 * i);
	}
	printf("adreno8: %u: the GMU's data memory begins %#x %#x\n", adreno8_step, save[0], save[1]);
	adreno8_wr(adreno8_gmu.base + GMU_DTCM, first);
	adreno8_wr(adreno8_gmu.base + GMU_DTCM + 4, second);
	if (adreno8_rd(adreno8_gmu.base + GMU_DTCM) != first || adreno8_rd(adreno8_gmu.base + GMU_DTCM + 4) != second) {
		printf("adreno8: %u: the GMU's memory stays deaf (%#x %#x)\n", adreno8_step, adreno8_rd(adreno8_gmu.base + GMU_DTCM),
		    adreno8_rd(adreno8_gmu.base + GMU_DTCM + 4));
		return false;
	}
	printf("adreno8: %u: THE GMU'S MEMORY IS LISTENING: %08x %08x went in and came back\n", adreno8_step, first, second);
	adreno8_announce("put back what was there");
	adreno8_wr(adreno8_gmu.base + GMU_DTCM, save[0]);
	adreno8_wr(adreno8_gmu.base + GMU_DTCM + 4, save[1]);
	return true;
}

/* ---- step 9: memory for the GMU, and its translation ------------------------------------------------------ */

static bool adreno8_image_asked_quietly;            /* adreno8_image_file() says nothing: is the file there at all */

static const uint8_t *
adreno8_image_file(const char *directory, const char *name, uint64_t *size)
{
	int disk = -1;
	const uint8_t *data = redwood_image_file(directory, name, size, &disk);  /* (redwood_image.c) */

	if (data != NULL) {
		if (!adreno8_image_asked_quietly) {
			printf("adreno8: %u: %s/%s: %llu bytes in the image of /dev/md%d\n", adreno8_step, directory, name, *size, disk);
		}
		return data;
	}
	if (!adreno8_image_asked_quietly) {
		printf("adreno8: %u: no %s/%s in the small image: put the phone's /vendor/firmware_mnt/image/%s into fw/ and run "
		    "userland/build.sh\n", adreno8_step, directory, name, name);
	}
	return NULL;
}

static const uint8_t *adreno8_gmu_firmware;

static uint32_t adreno8_gmu_firmware_size;

/* Whether an address of the firmware file's space is one of its two processor memories. */

static bool
adreno8_in_tcm(uint32_t address)
{
	return address < GMU_TCM_WORDS * 4 || (address >= GMU_DTCM_ADDRESS && address < GMU_DTCM_ADDRESS + GMU_TCM_WORDS * 4);
}

/* The memory we keep for the GMU whether its file asks for it or not (Linux: a6xx_gmu's four). */

static const struct {
	const char *name;
	uint32_t address, size;
} adreno8_gmu_fixed[] = {
	{ "log",   0x60002000, 0x4000 },        /* where its firmware writes what it has to say (GENERAL_8) */
	{ "hfi",   0x60006000, 0x4000 },        /* queue table, then command queue, then response queue */
	{ "debug", 0x60400000, 0x7000 },        /* the firmware's debug region, whatever it does with it */
	{ "dummy", 0x60000000, 0x4000 },        /* an address to point at when nothing else is meant */
};

/*
 * The firmware file's first pass, before any of it runs: every zero-size segment is a request
 * for memory at a stated address (Linux: seg.type == SCM_NOCACHE_REQ); we make exactly that much,
 * at exactly that address in the GMU's space. Then the four regions the driver keeps anyway.
 * Then the queue table is filled where the file says the queues live, and the context gets its
 * page tables, one 4K page of the memory above at the same 4K address the firmware asked for.
 */

bool
adreno8_step_gmu_space(void)
{
	uint32_t at, wanted = 0, demands = 0, address, size, type;
	uint32_t *table;

	if (!adreno8_cx_ready()) {
		return false;
	}
	if (adreno8_gmu_firmware == NULL) {
		uint64_t bytes = 0;

		adreno8_gmu_firmware = adreno8_image_file("fw", "gen80200_gmu.bin", &bytes);
		if (adreno8_gmu_firmware == NULL) {
			return false;
		}
		adreno8_gmu_firmware_size = (uint32_t)bytes;
	}
	adreno8_announce("walk the GMU's firmware file for the memory it asks for, and make it");
	for (at = 0; at + 16 <= adreno8_gmu_firmware_size; at += 16 + size) {
		address = ((const uint32_t *)(adreno8_gmu_firmware + at))[0];
		size = ((const uint32_t *)(adreno8_gmu_firmware + at))[1];
		type = ((const uint32_t *)(adreno8_gmu_firmware + at))[2];
		if (size != 0 || (type != 1 && type != 7)) {
			continue;
		}
		if (adreno8_in_tcm(address)) {
			printf("adreno8: %u: the file wants %#x bytes of the processor's own memory at %#x: it will have to do with the 16K there\n",
			    adreno8_step, type == 7 ? 0x1000000 : 0x8000, address);
			continue;
		}
		if (adreno8_covered(address, size)) {
			printf("adreno8: %u: the file asks again for %#x bytes at %#x: already had it\n", adreno8_step, size, address);
			continue;
		}
		if (!adreno8_block_new(&adreno8_gmu_demands[adreno8_gmu_demand_count], "what the file asked for", address, size)) {
			return false;
		}
		wanted += size;
		demands++;
	}
	printf("adreno8: %u: %u blocks, %#x bytes all, made where the firmware asked\n", adreno8_step, demands, wanted);

	adreno8_announce("and the memory we keep for it whatever the file says: its log, its queues, a debug scratch, a dummy page");
	for (unsigned i = 0; i < 4; i++) {
		if (adreno8_covered(adreno8_gmu_fixed[i].address, adreno8_gmu_fixed[i].size)) {
			printf("adreno8: %u: NOTE: the file asked for %#x bytes at %#x, where %s was to live: the file's block wins; the views of log and queues may read the wrong pages\n",
			    adreno8_step, adreno8_gmu_fixed[i].size, adreno8_gmu_fixed[i].address, adreno8_gmu_fixed[i].name);
		}
	}
	if (!adreno8_block_new(&adreno8_gmu_log, "gmu log", adreno8_gmu_fixed[0].address, adreno8_gmu_fixed[0].size) ||
	    !adreno8_block_new(&adreno8_gmu_hfi, "gmu hfi", adreno8_gmu_fixed[1].address, adreno8_gmu_fixed[1].size)) {
		return false;
	}
	if (!adreno8_block_new(&adreno8_gmu_debug, "gmu debug", adreno8_gmu_fixed[2].address, adreno8_gmu_fixed[2].size) ||
	    !adreno8_block_new(&adreno8_gmu_dummy, "gmu dummy", adreno8_gmu_fixed[3].address, adreno8_gmu_fixed[3].size)) {
		printf("adreno8: %u: (kept going without it: the firmware will use what it can)\n", adreno8_step);
	}

	adreno8_announce("fill the queue table where the queues live: the table, the command queue behind it, the response queue behind that (Linux: a6xx_hfi_init)");
	table = (uint32_t *)adreno8_gmu_hfi.memory;
	table[0] = 0;                           /* the table's own version */
	table[1] = 120;                         /* the size of what follows, in dwords: two queues of 12 plus headers */
	table[2] = 6;                           /* where the queues start, in dwords */
	table[3] = 12;                          /* the size of one queue's header */
	table[4] = 2;                           /* how many queues */
	table[5] = 2;                           /* and ... again: two, by the firmware's own format */
	for (unsigned q = 0; q < 2; q++) {
		uint32_t *h = table + (q == 0 ? 24 : 72);

		h[0] = 1;                        /* status: on */
		h[1] = 0x60006000 + 0x1000 * (q + 1);     /* the queue's own memory: pages 1 and 2 */
		h[2] = (10u << 8) | q;           /* its version and number */
		h[3] = 1024;                     /* dwords in it */
		h[4] = 0;                        /* message size, if fixed: not here */
		h[5] = 0;                        /* dropped so far */
		h[6] = 1;                        /* the watermark to be poked about */
		h[7] = 1;
		h[8] = 1;                        /* ask to be told when something arrives */
		h[9] = 0;
		h[10] = 0;                       /* read index */
		h[11] = 0;                       /* write index */
	}
	for (unsigned i = 96; i < 1024; i++) {
		table[i] = 0xfafafafa;          /* Linux: "any accidental read is obviously junk" */
	}
	adreno8_to_memory(adreno8_gmu_hfi.memory, PAGE_SIZE);

	adreno8_announce("uncached views of the queues and the log: the GMU writes while our cache can only guess");
	adreno8_hfi_view_table = ml_io_map_wcomb(kvtophys(adreno8_gmu_hfi.memory), PAGE_SIZE);
	adreno8_hfi_view_cmd = ml_io_map_wcomb(kvtophys(adreno8_gmu_hfi.memory + PAGE_SIZE), PAGE_SIZE);
	adreno8_hfi_view_resp = ml_io_map_wcomb(kvtophys(adreno8_gmu_hfi.memory + 2 * PAGE_SIZE), PAGE_SIZE);
	for (unsigned i = 0; i < 4; i++) {
		adreno8_log_view[i] = ml_io_map_wcomb(kvtophys(adreno8_gmu_log.memory + i * PAGE_SIZE), PAGE_SIZE);
	}

	adreno8_announce("map every block into the GMU's context at the address it asked for, and turn that context on");
	if (adreno8_space_new_table(&adreno8_gmu_space, 0, 0) == 0) {
		return false;
	}
	if (!adreno8_space_map_block(&adreno8_gmu_space, &adreno8_gmu_log) || !adreno8_space_map_block(&adreno8_gmu_space, &adreno8_gmu_hfi)) {
		return false;
	}
	for (unsigned i = 0; i < 4; i++) {
		const struct adreno8_gpu_block *b = i == 0 ? &adreno8_gmu_debug : i == 1 ? &adreno8_gmu_dummy : NULL;

		if (b != NULL && b->memory != 0 && !adreno8_space_map_block(&adreno8_gmu_space, b)) {
			return false;
		}
	}
	for (unsigned i = 0; i < adreno8_gmu_demand_count; i++) {
		if (!adreno8_space_map_block(&adreno8_gmu_space, &adreno8_gmu_demands[i])) {
			return false;
		}
	}
	return adreno8_space_on(&adreno8_gmu_space, GMU_STREAM, GMU_MATCH);
}

/* ---- step 10: the firmware itself ------------------------------------------------------------------------- */

/*
 * The file's segments, into the GMU's memories and memory (Linux: a6xx_gmu_load_adreno_image):
 * the two processor memories go in through the window, everything else was made in step 9 into
 * our own memory at the address the file states, and the GMU's translation will find it there.
 * Written once, then read back: redwood's two passes, because on the first boot of a new phone a
 * wrong number of bytes somewhere shows up here and not in a crash nine steps later.
 */

bool
adreno8_step_firmware(void)
{
	uint32_t at, address, size, written = 0, wrong = 0, pass;

	if (!adreno8_cx_ready()) {
		return false;
	}
	if (adreno8_rd(adreno8_gmu.base + GMU_FW_INIT_RESULT) == GMU_FW_ALREADY) {
		printf("adreno8: %u: the GMU's firmware is running already; only its memory, once, before it starts\n", adreno8_step);
		return false;
	}
	if (adreno8_gmu_firmware == NULL) {
		uint64_t bytes = 0;

		adreno8_gmu_firmware = adreno8_image_file("fw", "gen80200_gmu.bin", &bytes);
		if (adreno8_gmu_firmware == NULL) {
			return false;
		}
		adreno8_gmu_firmware_size = (uint32_t)bytes;
	}
	if (!adreno8_gmu_space.is_on) {
		printf("adreno8: %u: its translation is not up: step 9 has to come first\n", adreno8_step);
		return false;
	}
	adreno8_announce("put the firmware in: the two processor memories through the window, the rest into the memory made for it, and read it all back");
	for (pass = 0; pass < 2; pass++) {
		wrong = 0;
		for (at = 0; at + 16 <= adreno8_gmu_firmware_size; at += 16 + size) {
			address = ((const uint32_t *)(adreno8_gmu_firmware + at))[0];
			size = ((const uint32_t *)(adreno8_gmu_firmware + at))[1];
			if (size == 0) {
				continue;             /* a demand: step 9 dealt with it */
			}
			if (at + 16 + size > adreno8_gmu_firmware_size) {
				printf("adreno8: %u: a segment at %#x claims %#x bytes and the file ends: the file is wrong\n", adreno8_step, address, size);
				return false;
			}
			if (adreno8_in_tcm(address)) {
				uint32_t in_itcm = address < GMU_TCM_WORDS * 4;
				uint32_t base = in_itcm ? GMU_ITCM : GMU_DTCM;
				uint32_t off = in_itcm ? address : address - GMU_DTCM_ADDRESS;
				const uint32_t *words = (const uint32_t *)(adreno8_gmu_firmware + at + 16);

				if (off + size > GMU_TCM_WORDS * 4) {
					printf("adreno8: %u: a segment wants %#x bytes at %#x: more than the 16K there\n", adreno8_step, size, address);
					return false;
				}
				for (uint32_t i = 0; i < size / 4; i++) {
					if (pass == 0) {
						adreno8_wr(adreno8_gmu.base + base + off + 4 * i, words[i]);
					} else if (adreno8_rd(adreno8_gmu.base + base + off + 4 * i) != words[i]) {
						wrong++;
					}
				}
				written += size;
				continue;
			}
			{
				vm_offset_t where = adreno8_seen_at(address);

				if (where == 0) {
					printf("adreno8: %u: a segment wants %#x bytes at %#x which step 9 did not make: refusing\n", adreno8_step, size, address);
					return false;
				}
				if (pass == 0) {
					memcpy((void *)where, adreno8_gmu_firmware + at + 16, size);
					adreno8_to_memory(where, size);
				} else if (memcmp((const void *)where, adreno8_gmu_firmware + at + 16, size) != 0) {
					wrong++;
				}
				written += size;
			}
		}
		if (pass == 1 && wrong != 0) {
			printf("adreno8: %u: %u segments out of the ones that hold %#x bytes do not read back right\n", adreno8_step, wrong, written);
			return false;
		}
	}
	printf("adreno8: %u: THE FIRMWARE IS IN: %#x bytes, every one read back\n", adreno8_step, written);
	adreno8_fw_loaded = true;
	return true;
}

bool
adreno8_step_gmu_start(void)
{
	uint32_t version, mask, reset_val;

	if (!adreno8_cx_ready()) {
		return false;
	}
	if (!adreno8_fw_loaded) {
		printf("adreno8: %u: the firmware is not in its memory: step 10\n", adreno8_step);
		return false;
	}
	adreno8_announce("clear every interrupt the GMU might be holding from before, and let through only the fault and the three we read ourselves");
	adreno8_put(&adreno8_gmu, "ao interrupt clr", GMU_AO_INTERRUPT_CLR, ~0u);
	adreno8_put(&adreno8_gmu, "ao interrupt mask", GMU_AO_INTERRUPT_MASK, GMU_AO_KEEP);
	adreno8_put(&adreno8_gmu, "gmu2host clr", GMU_GMU2HOST_CLR, ~0u);

	if (adreno8_rsc_asleep_at_first) {
		uint32_t req = adreno8_get(&adreno8_gmu, "rscc control req", GMU_RSCC_CONTROL_REQ);

		adreno8_announce("the RSC that lets the GMU ask RPMh for things was found asleep: ask through the GMU's own bit for it to wake, and wait a tenth of a second for the ack (the vendor's driver waits the same and does not fail on it)");
		if ((adreno8_get(&adreno8_gmu, "rscc control ack", GMU_RSCC_CONTROL_ACK) & 2) == 0) {
			adreno8_put(&adreno8_gmu, "rscc control req", GMU_RSCC_CONTROL_REQ, req | 2);
			if (adreno8_wait_for(&adreno8_gmu, GMU_RSCC_CONTROL_ACK, 2, 2, 100)) {
				printf("adreno8: %u: the RSC woke\n", adreno8_step);
			} else {
				printf("adreno8: %u: the RSC does not ack in 100 ms (ack %#x, its sequence busy word %#x): going on anyway\n", adreno8_step,
				    adreno8_rd(adreno8_gmu.base + GMU_RSCC_CONTROL_ACK), adreno8_rscc.base != 0 ? adreno8_rd(adreno8_rscc.base + RSCC_SEQ_BUSY) : 0);
			}
			adreno8_rsc_woken_by_us = true;
		}
	}

	adreno8_announce("write the words the firmware is started with (Linux: a6xx_gmu_fw_start, a8xx branch, verbatim in order and value)");
	adreno8_put(&adreno8_gmu, "general 10: what chip the firmware is told it runs on (catalog gen80200 chip_id)", GMU_GENERAL_10, 0x08020100);
	adreno8_put(&adreno8_gmu, "general 8: where its log is, and how many pages minus one", GMU_GENERAL_8,
	    (adreno8_gmu_log.address & ~0xfff) | ((adreno8_gmu_log.size / 4096 - 1) & 0xff));
	adreno8_put(&adreno8_gmu, "general 9: where in the log it writes (we start it at the beginning)", GMU_GENERAL_9, 0);
	adreno8_put(&adreno8_gmu, "cm3 config", GMU_CM3_CFG, 0x4052);
	adreno8_put(&adreno8_gmu, "ahb fence range 0: what the GMU may not touch until the firmware says it may", GMU_AHB_FENCE_RANGE_0,
	    1u << 31 | (0x32u & 0x1fff) << 18 | 0x8c0);
	adreno8_put(&adreno8_gmu, "falnext intf", GMU_CX_FALNEXT_INTF, 1);
	adreno8_put(&adreno8_gmu, "fal intf", GMU_CX_FAL_INTF, 1);
	adreno8_put(&adreno8_gmu, "fw init result: cleared, so what comes means the firmware's own doing", GMU_FW_INIT_RESULT, 0);
	adreno8_put(&adreno8_gmu, "cm3 boot config", GMU_CM3_BOOT_CONFIG, 0x02);
	adreno8_put(&adreno8_gmu, "hfi qtbl address: the queue table's address in its space", GMU_HFI_QTBL_ADDR, 0x60006000);
	adreno8_put(&adreno8_gmu, "hfi qtbl info", GMU_HFI_QTBL_INFO, 1);
	adreno8_put(&adreno8_gmu, "icache config", GMU_ICACHE_CONFIG, 1);
	adreno8_put(&adreno8_gmu, "dcache config", GMU_DCACHE_CONFIG, 1);
	adreno8_put(&adreno8_gmu, "sys bus config", GMU_SYS_BUS_CONFIG, 1);
	adreno8_put(&adreno8_gmu, "mrc gbif qos", GMU_MRC_GBIF_QOS, 0x33);
	adreno8_put(&adreno8_gpu, "gbif cx config (the bus gates' own registers, on the 3D core's front door: Linux's a8xx power up)",
	    GPU_GBIF_CX_CONFIG, GPU_GBIF_CX_VALUE);
	adreno8_put(&adreno8_gpu, "gbif qsb side 0", GPU_GBIF_QSB_SIDE0, GPU_GBIF_QSB_VALUE);
	adreno8_put(&adreno8_gpu, "gbif qsb side 1", GPU_GBIF_QSB_SIDE0 + 4, GPU_GBIF_QSB_VALUE);
	adreno8_put(&adreno8_gpu, "gbif qsb side 2", GPU_GBIF_QSB_SIDE0 + 8, GPU_GBIF_QSB_VALUE);
	adreno8_put(&adreno8_gpu, "gbif qsb side 3", GPU_GBIF_QSB_SIDE0 + 12, GPU_GBIF_QSB_VALUE);
	adreno8_get(&adreno8_gmu, "rpmh ctrl: the a8xx branch of Linux's fw_start writes no more and returns; read for the record", GMU_RPMH_CTRL);

	version = adreno8_get(&adreno8_gmu, "the word at the end of its data memory, which says what sort of firmware went in", GMU_DTCM_VERSIONS);
	if (version <= 0x20010004) {
		mask = 0xffffffff;
		reset_val = GMU_FW_ALREADY;     /* older firmware's word for "I am running" */
	} else {
		mask = GMU_FW_STARTED_MASK;
		reset_val = GMU_FW_STARTED;
	}
	printf("adreno8: %u: the result word will have to be %#x out of %#x (firmware word %#x: %s firmware)\n", adreno8_step, reset_val,
	    mask, version, version <= 0x20010004 ? "older" : "newer");

	adreno8_announce("and let its processor out of reset, waiting a second for its own verdict (Linux: a6xx_gmu_start, verbatim)");
	adreno8_put(&adreno8_gmu, "cm3 sysreset", GMU_CM3_SYSRESET, 1);
	adreno8_put(&adreno8_gmu, "general 9: the log's write index, which the boot loader's firmware may have left anywhere", GMU_GENERAL_9, 0);
	adreno8_put(&adreno8_gmu, "cm3 sysreset: go", GMU_CM3_SYSRESET, 0);
	if (!adreno8_wait_for(&adreno8_gmu, GMU_FW_INIT_RESULT, mask, reset_val, 1000)) {
		printf("adreno8: %u: a second passed and the result word says %#x, which is not %#x out of %#x%s\n", adreno8_step,
		    adreno8_rd(adreno8_gmu.base + GMU_FW_INIT_RESULT), reset_val, mask,
		    adreno8_rd(adreno8_gmu.base + GMU_FW_INIT_RESULT) & GMU_FW_FAILED ? " and carries failure bits" : "");
		adreno8_gmu_post_mortem();
		return false;
	}
	adreno8_get(&adreno8_gmu, "fw init result", GMU_FW_INIT_RESULT);
	printf("adreno8: %u: THE GMU'S FIRMWARE RUNS: its result word %#x, its log at %#x\n", adreno8_step,
	    adreno8_rd(adreno8_gmu.base + GMU_FW_INIT_RESULT), adreno8_gmu_log.address);
	adreno8_gmu_runs = true;
	return true;
}
