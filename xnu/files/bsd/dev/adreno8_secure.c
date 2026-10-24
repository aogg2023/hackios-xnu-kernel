/* MP_ASSIGN ownership transfer backing the HFI kind-5 secure allocation. */
#include "adreno8_secure_private.h"

#define printf adreno8_say

/* QCOM_SCM_SVC_MP (0x0c) + QCOM_SCM_MP_ASSIGN (0x16) in the SMC64 standard call
 * encoding; the arginfo matches the vendor's seven-argument type list
 * (RO, VAL, RO, VAL, RO, VAL, VAL). Arguments 3..6 travel in the extended
 * buffer whose physical address occupies register slot x5. */
#define SCM_MP_ASSIGN        0x42000c16ull
#define SCM_ASSIGN_ARGINFO   0x1117ull
#define ASSIGN_SECTIONS      32u       /* vendor BATCH_MAX_SECTIONS: region entries per SCM call */
#define ASSIGN_EBUSY         (-12)
#define ASSIGN_EBUSY_RETRY   20u       /* vendor QCOM_SCM_EBUSY_MAX_RETRY at 30 ms spacing */
#define ASSIGN_EBUSY_MS      30u
#define VMID_HLOS            3u
#define VMID_CP_PIXEL        0xAu
#define PERM_READ            4u
#define PERM_WRITE           2u
#define PERM_EXEC            1u
#define PT_LEAF_GRANULE      4096u     /* adreno8_smmu.c maps leaves at 4 KiB */

/* Little-endian wire records; this kernel is arm64-only, so plain stores match
 * the vendor's cpu_to_le64 layout exactly. */
struct assign_region {
	uint64_t address, size;
};
struct assign_perm {
	uint32_t vmid, perm;
	uint64_t context;
	uint32_t context_size, unused;
};

#define ASSIGN_SOURCES      512u       /* after 32 region slots */
#define ASSIGN_PERMS        576u       /* 64-byte aligned, room for two entries */
#define ASSIGN_EXTENDED     640u       /* four little-endian argument words */
#define ASSIGN_USED         (ASSIGN_EXTENDED + 4 * sizeof(uint64_t))

/* The layout above is the TrustZone-side ABI: record sizes come from the
 * vendor wire format and every byte must stay inside the one argument page. */
_Static_assert(sizeof(struct assign_region) == 16, "MP_ASSIGN region record must be 16 bytes");
_Static_assert(sizeof(struct assign_perm) == 24, "MP_ASSIGN permission record must be 24 bytes");
_Static_assert(ASSIGN_USED <= PAGE_SIZE, "MP_ASSIGN argument layout must fit one page");

static struct {
	vm_offset_t page;
	uint64_t page_phys;
	bool lost;
	unsigned attempts;               /* SCM calls actually made, retries included */
} adreno8_assign;

bool
adreno8_secure_lost(void)
{
	return adreno8_assign.lost;
}

/* One physically contiguous, zeroed argument page; kvtophys is verified across
 * the whole page like the zap metadata buffer. A page that fails verification
 * here is freed and retried on the next call instead of marking `lost`: the
 * argument page is a plain HLOS page never handed to the secure world, so its
 * ownership is never in doubt. */
static bool
adreno8_assign_open(void)
{
	uint64_t first;

	if (adreno8_assign.lost) {
		return false;
	}
	if (adreno8_assign.page != 0) {
		return true;
	}
	if (kmem_alloc(kernel_map, &adreno8_assign.page, PAGE_SIZE,
	    KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		adreno8_assign.page = 0;
		return false;
	}
	first = kvtophys(adreno8_assign.page);
	if (first == 0 || (first & PAGE_MASK) ||
	    kvtophys(adreno8_assign.page + PAGE_SIZE - 1) != first + PAGE_SIZE - 1) {
		kmem_free(kernel_map, adreno8_assign.page, PAGE_SIZE);
		adreno8_assign.page = 0;
		return false;
	}
	adreno8_assign.page_phys = first;
	printf("adreno8: MP_ASSIGN argument page at %#llx\n", first);
	return true;
}

static bool
adreno8_assign_call(uint64_t regions_bytes, uint64_t perms_bytes)
{
	uint32_t *sources = (uint32_t *)(adreno8_assign.page + ASSIGN_SOURCES);
	uint64_t *extended = (uint64_t *)(adreno8_assign.page + ASSIGN_EXTENDED);
	uint64_t sources_phys = adreno8_assign.page_phys + ASSIGN_SOURCES;
	uint64_t perms_phys = adreno8_assign.page_phys + ASSIGN_PERMS;
	uint64_t extended_phys = adreno8_assign.page_phys + ASSIGN_EXTENDED;
	uint64_t answer = 1;
	int32_t status = ASSIGN_EBUSY;

	sources[0] = VMID_HLOS;
	extended[0] = sizeof(uint32_t);
	extended[1] = perms_phys;
	extended[2] = perms_bytes;
	extended[3] = 0;
	/* TZ reads every staged record from DRAM: clean after the last store. */
	adreno8_to_memory(adreno8_assign.page, ASSIGN_USED);
	/*
	 * The retry bound counts EBUSY retries; it is not a wall-clock bound.
	 * A single SMC may not return at all, and then no host-side timeout
	 * cancels the TrustZone call; a host timeout only bounds host waiting.
	 */
	for (unsigned retry = 0; retry < ASSIGN_EBUSY_RETRY; retry++) {
		adreno8_assign.attempts++;
		status = adreno8_scm_call(SCM_MP_ASSIGN, SCM_ASSIGN_ARGINFO, adreno8_assign.page_phys,
		    regions_bytes, sources_phys, extended_phys, &answer);
		if (status != ASSIGN_EBUSY) {
			break;
		}
		adreno8_sleep_ms(ASSIGN_EBUSY_MS);
	}
	if (status == 0 && answer == 0) {
		return true;
	}
	printf("adreno8: MP_ASSIGN returned %d, answer %#llx\n", status, answer);
	return false;
}

/* The caller has staged `count` region entries in @regions. A failed batch can
 * have been applied to TZ partially and the HLOS cannot tell which regions
 * landed, so every page of this operation stays assigned where it is until
 * cold reset; nothing here rolls back or frees. */
static bool
adreno8_assign_flush(const struct assign_region *regions, unsigned count,
    const struct assign_perm *perms, unsigned perm_count)
{
	struct assign_region *slots = (struct assign_region *)adreno8_assign.page;
	struct assign_perm *perm_slots = (struct assign_perm *)(adreno8_assign.page + ASSIGN_PERMS);

	for (unsigned i = 0; i < count; i++) {
		slots[i] = regions[i];
	}
	for (unsigned i = 0; i < perm_count; i++) {
		perm_slots[i] = perms[i];
	}
	if (!adreno8_assign_call((uint64_t)count * sizeof(struct assign_region),
	    (uint64_t)perm_count * sizeof(struct assign_perm))) {
		adreno8_assign.lost = true;
		printf("adreno8: secure pages retained until cold reset\n");
		return false;
	}
	return true;
}

/* Stage and flush the buffer's data pages to CP_PIXEL read/write. The caller has
 * passed the preflight and opened the argument page. */
static bool
adreno8_assign_data(const struct adreno8_memory *buffer)
{
	struct assign_perm perms[] = { { VMID_CP_PIXEL, PERM_READ | PERM_WRITE, 0, 0, 0 } };
	struct assign_region batch[ASSIGN_SECTIONS];
	uint64_t bytes = ((uint64_t)buffer->size + PAGE_MASK) & ~(uint64_t)PAGE_MASK;
	unsigned filled = 0;

	for (uint64_t at = 0; at < bytes; ) {
		uint64_t phys = kvtophys(buffer->memory + at), run = PAGE_SIZE;

		if (phys == 0 || (phys & PAGE_MASK)) {
			/*
			 * A page whose physical start cannot be proven closes the secure path for
			 * the whole boot, whether or not an earlier batch already ran. Before the
			 * first MP_ASSIGN the pages are still HLOS-owned and the cost is only a
			 * closed secure path; after one, the driver cannot learn which regions TZ
			 * accepted, so every page of this buffer stays reserved until cold reset.
			 * The check never runs against a page TZ may own, so no CPU read probes a
			 * transferred page for recovery; a cold reset recovering a first handover
			 * is itself unconfirmed and needs device-side confirmation.
			 */
			printf("adreno8: secure page %#llx unusable for MP_ASSIGN\n", phys);
			adreno8_assign.lost = true;
			printf("adreno8: secure pages retained until cold reset\n");
			return false;
		}
		while (at + run < bytes && kvtophys(buffer->memory + at + run) == phys + run) {
			run += PAGE_SIZE;
		}
		batch[filled++] = (struct assign_region){ phys, run };
		at += run;
		if (filled == ASSIGN_SECTIONS) {
			if (!adreno8_assign_flush(batch, filled, perms, 1)) {
				return false;
			}
			filled = 0;
		}
	}
	if (filled != 0 && !adreno8_assign_flush(batch, filled, perms, 1)) {
		return false;
	}
	printf("adreno8: secure buffer %s: %llu KiB owned by CP_PIXEL\n",
	    buffer->name, (unsigned long long)(bytes / 1024));
	return true;
}

/* Assign page-table pages past the cursor to {HLOS read/write, CP_PIXEL read}.
 * The caller opened the argument page; committing the cursor is the caller's. */
static bool
adreno8_assign_tables(struct adreno8_gpu_space *space)
{
	struct assign_perm perms[] = {
		{ VMID_HLOS, PERM_READ | PERM_WRITE, 0, 0, 0 },
		{ VMID_CP_PIXEL, PERM_READ, 0, 0, 0 },
	};
	struct assign_region batch[ASSIGN_SECTIONS];
	unsigned filled = 0;

	for (unsigned i = space->secure_tables; i < space->table_count; i++) {
		if (space->tables[i].table == 0 || space->tables[i].phys == 0) {
			/* Same conservative rule as adreno8_assign_data: an unproven page ends
			 * the secure path for the boot and every table page stays reserved. */
			adreno8_assign.lost = true;
			printf("adreno8: secure pages retained until cold reset\n");
			return false;
		}
		/* The walker only consumes the first 4 KiB; the whole kernel page is ours. */
		adreno8_to_memory(space->tables[i].table, PT_LEAF_GRANULE);
		batch[filled++] = (struct assign_region){ space->tables[i].phys, PAGE_SIZE };
		if (filled == ASSIGN_SECTIONS) {
			if (!adreno8_assign_flush(batch, filled, perms, 2)) {
				return false;
			}
			filled = 0;
		}
	}
	if (filled != 0 && !adreno8_assign_flush(batch, filled, perms, 2)) {
		return false;
	}
	return true;
}

/* Count the data pages and merge runs into region entries without staging, so
 * an experiment can print its batch plan before any MP_ASSIGN. The counts come
 * from the real PAGE_SIZE and the merge rule adreno8_assign_data uses. */
static bool
adreno8_assign_plan(const struct adreno8_memory *buffer, unsigned *pages, unsigned *regions)
{
	uint64_t bytes = ((uint64_t)buffer->size + PAGE_MASK) & ~(uint64_t)PAGE_MASK;

	*pages = 0;
	*regions = 0;
	for (uint64_t at = 0; at < bytes; ) {
		uint64_t phys = kvtophys(buffer->memory + at), run = PAGE_SIZE;

		if (phys == 0 || (phys & PAGE_MASK)) {
			printf("adreno8: secure page %#llx unusable for MP_ASSIGN\n", phys);
			return false;
		}
		while (at + run < bytes && kvtophys(buffer->memory + at + run) == phys + run) {
			run += PAGE_SIZE;
		}
		*pages += (unsigned)(run / PAGE_SIZE);
		*regions += 1;
		at += run;
	}
	return true;
}

/*
 * Isolated handover experiment state. The boot-args mode gates entry; this
 * state makes the attempt one-shot and terminal. Not started allows one handover;
 * attempted is set before any MP_ASSIGN goes out so a repeated request cannot
 * start a second one; terminal closes the path for the rest of the boot whether
 * the handover succeeded or failed.
 */
static uint32_t adreno8_xfer_state;

uint32_t
adreno8_secure_xfer_state(void)
{
	return adreno8_xfer_state;
}

/* Close the experiment when the driver cannot reach the first MP_ASSIGN (no
 * usable page, no argument page). Nothing was transferred, so no page is
 * marked lost; the state still ends the secure path for this boot. Once an
 * attempt went out the caller owns the outcome: this must not overwrite an
 * attempted state or claim no page transferred after a real call. */
void
adreno8_secure_xfer_abort(void)
{
	if (adreno8_xfer_state != SECURE_XFER_NOT_STARTED) {
		return;
	}
	adreno8_xfer_state = SECURE_XFER_TERMINAL;
	printf("adreno8: secure handover ends before any MP_ASSIGN; no page transferred\n");
}

/*
 * One isolated handover of this kernel's own kind-5 buffer: assign the secure
 * page-table pages, then the data pages, and stop. The caller runs
 * adreno8_secure_preflight first; this function is reached only from that one
 * kernel call site. No MEM_ALLOC reply, no CB3 enable, no CB0 mapping and no
 * address reaches the firmware. Whatever the outcome, the attempt is terminal
 * and the pages stay retained; a success keeps them with the secure owner
 * rather than marking them lost. This does not prove the secure bridge can
 * hand memory back, a path the earlier park and PSCI checks never exercised.
 */
bool
adreno8_secure_xfer(struct adreno8_gpu_space *space, const struct adreno8_memory *buffer)
{
	unsigned pages, regions, batches;

	if (adreno8_xfer_state != SECURE_XFER_NOT_STARTED || buffer->memory == 0) {
		return false;
	}
	if (!adreno8_assign_plan(buffer, &pages, &regions) || !adreno8_assign_open()) {
		adreno8_secure_xfer_abort();
		return false;
	}
	adreno8_xfer_state = SECURE_XFER_ATTEMPTED;
	batches = (regions + ASSIGN_SECTIONS - 1) / ASSIGN_SECTIONS +
	    (space->table_count - space->secure_tables + ASSIGN_SECTIONS - 1) / ASSIGN_SECTIONS;
	printf("adreno8: secure handover attempted: %u pages merge into %u data regions plus %u table pages, %u logical MP_ASSIGN batches\n",
	    pages, regions, space->table_count - space->secure_tables, batches);
	if (!adreno8_assign_tables(space) || !adreno8_assign_data(buffer)) {
		adreno8_xfer_state = SECURE_XFER_TERMINAL;
		return false;
	}
	space->secure_tables = space->table_count;
	adreno8_xfer_state = SECURE_XFER_TERMINAL;
	printf("adreno8: secure handover complete: %u pages owned by CP_PIXEL after %u MP_ASSIGN attempts, retained until cold reset; no MEM_ALLOC reply\n",
	    pages, adreno8_assign.attempts);
	return true;
}
