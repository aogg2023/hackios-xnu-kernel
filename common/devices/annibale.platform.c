/*
 * annibale: paste into xnuloader/loader.c next to platform_redwood and add &platform_annibale to platforms[].
 * Every value is explained in docs/PORTING.md ("The loader").
 */
static const struct platform platform_annibale = {
	.name = "annibale",
	.io_base = 0x00400000, .io_size = 0x3FC00000,   /* TODO: one window over the SoC's peripherals, non-zero base */
	.gic_dist = 0x16000000, .gic_redist = 0x16080000,
	.uart = 0,                                      /* phones: no PL011 */
	.log_other_cpu = 1,
	.cores = 8, .core_shift = 8,                 /* MPIDR: 0x0..0x500 (six perf), 0x10000/0x10100 (two prime);
	                                              * new-device.sh read shift 0 because the CPU nodes carry
	                                              * phandle labels ("CPU0: cpu@0") its regex does not match */
	.small_cores = 0x3f,                           /* the six MIDR 0x514f0014 cores: lower capacity cluster */
	.fb_base = 0x0,                                 /* TODO: the framebuffer base the UEFI prints ("display ... at") */
};
