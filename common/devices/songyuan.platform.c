/*
 * songyuan: paste into xnuloader/platform_private.h next to platform_annibale and add
 * &platform_songyuan to platforms[].  GIC values come from the live SM8850 device tree
 * (distributor 0x17000000, redistributors 0x17080000 with a 0x40000 stride); the CPU
 * MPIDR list matches the stock DT / myron's SM8850 notes.
 */
static const struct platform platform_songyuan = {
	.name = "songyuan",
	.io_base = 0x00400000, .io_size = 0x3FC00000,   /* TODO: one window over the SoC's peripherals */
	.gic_dist = 0x17000000, .gic_redist = 0x17080000,
	.uart = 0,                                      /* phones: no PL011 */
	.log_other_cpu = 1,
	.cores = 8, .core_mpidr = {0, 0x100, 0x200, 0x300, 0x400, 0x500, 0x10000, 0x10100},
	.small_cores = 0x3f,
	.fb_base = 0x0,                                 /* TODO: the framebuffer base the UEFI prints */
};