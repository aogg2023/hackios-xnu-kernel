/* MP_ASSIGN ownership transfer backing the HFI kind-5 secure allocation. */
#ifndef _DEV_ADRENO8_SECURE_PRIVATE_H
#define _DEV_ADRENO8_SECURE_PRIVATE_H
#include "adreno8_memory_private.h"

/* Hand the allocation's data pages from HLOS to CP_PIXEL with read/write permission.
 * Pages are enumerated at kernel allocation-page granularity, so a 16 KiB page is
 * transferred whole. A true result means the CPU must never publish, invalidate or
 * read those pages again. */
bool adreno8_secure_data(const struct adreno8_memory *buffer);

/* Assign page-table pages newly appended to this space to {HLOS read/write,
 * CP_PIXEL read}, matching the vendor io-pgtable pool ownership. Only pages beyond
 * space->secure_tables are transferred; a true result commits the whole range. */
bool adreno8_secure_page_tables(struct adreno8_gpu_space *space);

/* True after any failed or ambiguous MP_ASSIGN batch. Assigned pages stay where
 * they are and no further secure assignment runs until a cold reset. */
bool adreno8_secure_lost(void);

/* Read-only occupancy preflight for the candidate secure route (stream 2,
 * match 2, context 3; implemented in adreno8_smmu.c). Both handover entry
 * points call it before staging anything; a false result rejects the handover
 * without writing registers or issuing SCM calls and leaves the uncertain
 * ownership flag alone, because nothing was transferred. A pass reports
 * HLOS-side register state only: the secure world's own reservations stay
 * invisible and translation behavior needs a device run. */
bool adreno8_secure_preflight(void);

/* One-shot state of the isolated handover experiment. Not started allows one
 * handover; attempted is set before the first MP_ASSIGN leaves the kernel and a
 * single SMC call may never return, so any later request must treat the boot as
 * terminal; terminal stays for the rest of the boot on success and on failure. */
#define SECURE_XFER_NOT_STARTED 0u
#define SECURE_XFER_ATTEMPTED   1u
#define SECURE_XFER_TERMINAL    2u

uint32_t adreno8_secure_xfer_state(void);

/* Close the experiment before any MP_ASSIGN goes out (no usable data page, no
 * argument page, or the driver could not stage the batch). Nothing transferred,
 * so no page is marked lost; the terminal state still ends the secure path for
 * the rest of the boot. */
void adreno8_secure_xfer_abort(void);

/* One isolated handover of the kernel's own kind-5 buffer: assign this space's
 * page-table pages, then the buffer's data pages, print the batch plan before
 * the first MP_ASSIGN, and stop. A true result means the CPU must never touch
 * those pages again; the pages stay retained whether the handover succeeded or
 * failed. Every later call returns false without issuing SCM calls. */
bool adreno8_secure_xfer(struct adreno8_gpu_space *space, const struct adreno8_memory *buffer);

#endif
