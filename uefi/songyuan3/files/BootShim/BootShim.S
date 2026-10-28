_Head:
  adr   x1, _Payload    /* Set _Entry address */
  b     _Start          /* Jump to the real code */

#if REQUIRES_KERNEL_HEADER == 1
_StackBase:
  .quad FD_BASE         /* Text Offset */

_StackSize:
  .quad FD_SIZE         /* Image Size */
  .quad 0               /* Flags */
  .quad 0               /* Reserved */
  .quad 0               /* Reserved */
  .quad 0               /* Reserved */
  .ascii "ARM\x64"      /* ARM64 Magic */
  .long 0               /* Reserved */
#endif

/*
 * Strict payload contract (annibale, PAYLOAD-MEM ruling 2026-10): the build
 * passes B = PAYLOAD_BASE and the container limit C = PAYLOAD_CAP together.
 * Without PAYLOAD_CAP the legacy shape below stays byte-for-byte unchanged
 * for redwood/QEMU. The checks assume the packer rules in mkxnuimg.py
 * (PAYLOAD_CAP mode): container file length F = 8 + N <= C, container bytes
 * padded up to align_up(F, 16), and at least 16 readable bytes at every
 * source container start P (no-payload images carry 16 zero bytes there).
 */
#if defined(PAYLOAD_CAP) && !defined(PAYLOAD_BASE)
#error "PAYLOAD_CAP requires PAYLOAD_BASE"
#endif

#if defined(PAYLOAD_BASE) && defined(PAYLOAD_CAP)
#if FD_BASE < 0 || PAYLOAD_BASE < 0 || FD_SIZE <= 0
#error "FD_BASE/PAYLOAD_BASE must be non-negative and FD_SIZE must be positive (a zero FD_SIZE still stores one 16-byte block, then subs wraps the loop budget to 2^64-16)"
#endif
#if (FD_BASE + FD_SIZE) > 0xFFFFFFFF
#error "the FD image must end inside the low 4 GiB (FD_BASE + FD_SIZE)"
#endif
#if (FD_SIZE % 16) != 0
#error "FD_SIZE must be a multiple of 16 (both copy loops step 16 bytes)"
#endif
#if (PAYLOAD_CAP % 16) != 0 || PAYLOAD_CAP < 32
#error "PAYLOAD_CAP must be a multiple of 16 and at least 32"
#endif
#if (PAYLOAD_BASE + PAYLOAD_CAP) > 0xFFFFFFFF
#error "the payload window must stay inside the low 4 GiB"
#endif
#if ((FD_BASE + FD_SIZE) > PAYLOAD_BASE) && ((PAYLOAD_BASE + PAYLOAD_CAP) > FD_BASE)
#error "the FD destination overlaps the payload window"
#endif
#endif

_Start:
  mov   x4, x1
  ldr   x5, =FD_BASE
#if defined(PAYLOAD_BASE) && defined(PAYLOAD_CAP)
  /*
   * S = x4 (FD source), D = FD_BASE in x5, Q = FD_SIZE, P = S + Q (source
   * container), window [B, B + C). The forward copy loops cannot serve an
   * overlap, so before the first store the code rejects every layout where
   * a destination write (FD copy, container copy, header clear) could
   * touch the source FD, the unread container or the running code. A
   * rejected layout stops at _Dead with every target byte untouched: the
   * old container header there may alias the source, clearing it first
   * could destroy what we still have to read.
   * The container header reaches the window with the first copied chunk,
   * not last: nothing else runs until the branch to FD_BASE, so no
   * consumer can observe a half-written header.
   */
  ldr   x7, =FD_SIZE
  adds  x10, x4, x7         /* P = S + Q */
  b.cs  _Dead               /* source start wrapped: layout outside the contract */
  ldr   w7, [x10]           /* container magic; the packer keeps >= 16 bytes readable at P */
  ldr   w9, [x10, #4]       /* N = body bytes */
  mov   x11, xzr            /* L = 0: no valid payload */
  movz  x12, #0x4E58
  movk  x12, #0x5055, lsl #16
  cmp   x7, x12
  b.ne  _PayHeadDone
  ldr   x12, =(PAYLOAD_CAP - 8)
  cmp   x9, x12
  b.hi  _PayHeadDone        /* N > C - 8: oversized, cleaned up like a missing payload */
  add   x11, x9, #23        /* L = align_up(8 + N, 16); N <= C - 8 and C % 16 keep L <= C */
  and   x11, x11, #~15
_PayHeadDone:
  /* Conservative separation of [P, P + C) from the window: N cannot prove
     how much of that span is backed, so the whole span counts as live.
     P + C cannot wrap: P < B + C and the build pins B + C below 2^32. */
  ldr   x12, =(PAYLOAD_BASE + PAYLOAD_CAP)
  cmp   x10, x12
  b.cs  _PayCode            /* P >= B + C: no overlap */
  ldr   x12, =PAYLOAD_CAP
  add   x7, x10, x12        /* P + C */
  ldr   x12, =PAYLOAD_BASE
  cmp   x12, x7
  b.cc  _Dead               /* B < P + C: overlap (includes P == B) */
_PayCode:
  /* Running code [A, S): LK picks the load address, so only run time can
     tell whether the window covers the instructions still executing. */
  adr   x9, _Head
  ldr   x12, =(PAYLOAD_BASE + PAYLOAD_CAP)
  cmp   x9, x12
  b.cs  _PayFd              /* code starts at/above the window end */
  ldr   x12, =PAYLOAD_BASE
  cmp   x12, x4
  b.cc  _Dead               /* window reaches into the running code */
_PayFd:
  cmp   x4, x5
  b.eq  _PayCopy            /* S == D: FD already in place, the container is still handled */
  ldr   x12, =(FD_BASE + FD_SIZE)
  cmp   x4, x12
  b.cs  _PayFdCode          /* source FD at/above the destination end: copy is disjoint */
  cmp   x5, x10             /* D < S + Q ? */
  b.cc  _Dead               /* [S, S + Q) overlaps [D, D + Q): forward copy unsupported */
_PayFdCode:
  /* The FD copy writes [D, D + Q): it may not overwrite the unread
     container source [P, P + C) ... */
  ldr   x12, =(FD_BASE + FD_SIZE)
  cmp   x12, x10
  b.ls  _PayFdEntry         /* FD destination ends at/before P */
  ldr   x12, =PAYLOAD_CAP
  add   x7, x10, x12        /* P + C */
  cmp   x5, x7
  b.cc  _Dead               /* FD destination reaches into the container source */
_PayFdEntry:
  /* ... nor the running code [A, S). */
  cmp   x5, x4
  b.cs  _PayFdCopy          /* FD destination starts at/above S: code lies below it */
  ldr   x7, =(FD_BASE + FD_SIZE)
  cmp   x9, x7
  b.cc  _Dead               /* code reaches into the FD destination */
_PayFdCopy:
  ldr   x6, =FD_SIZE

_PayFdLoop:
  ldp   x2, x3, [x4], #16
  stp   x2, x3, [x5], #16
  subs  x6, x6, #16
  b.ne  _PayFdLoop

_PayCopy:
  cbz   x11, _PayClear
  mov   x4, x10
  ldr   x5, =PAYLOAD_BASE
  mov   x6, x11

_PayLoop:
  ldp   x2, x3, [x4], #16
  stp   x2, x3, [x5], #16
  subs  x6, x6, #16
  b.ne  _PayLoop
  b     _Entry

_PayClear:
  /* Source known separate, payload missing/bad/oversized: clear the
     8-byte target header so the FD stops as "no payload" instead of
     booting the previous run's container from retained DDR. */
  ldr   x5, =PAYLOAD_BASE
  str   xzr, [x5]           /* exactly the 8-byte header */

  /* songyuan diagnostic: paint the LK splash (strict entry) */
  ldr   x9, =0xFC800000
  movz  w10, #0xFF00
  movk  w10, #0xFF00, lsl #16
  add   x11, x9, #0x100000
3:
  str   w10, [x9], #4
  cmp   x9, x11
  b.ne  3b
_Entry:
  ldr   x5, =FD_BASE
  br    x5
#else
  cmp   x4, x5
  beq   _ShimPaint
  ldr   x6,  =FD_SIZE

_CopyLoop:
  ldp   x2, x3, [x4], #16
  stp   x2, x3, [x5], #16
  subs  x6, x6, #16
  b.ne  _CopyLoop

#ifdef PAYLOAD_BASE
  /* x4 now points just past the FD: an optional "XNUP" payload may follow */
  ldr   w2, [x4]
  movz  w3, #0x4E58
  movk  w3, #0x5055, lsl #16
  cmp   w2, w3
  b.ne  _NoPayload
  ldr   w6, [x4, #4]    /* Payload Size */
  add   x6, x6, #23     /* Header + Round Up to 16  /* songyuan diagnostic: paint the LK splash so the screen shows the shim ran */
_ShimPaint:
  ldr   x9, =0xFC800000
  movz  w10, #0xFF00
  movk  w10, #0xFF00, lsl #16
  add   x11, x9, #0x100000
1:
  str   w10, [x9], #4
  cmp   x9, x11
  b.ne  1b
  b     _Entry

 */
  and   x6, x6, #~15
  movz  x5, #(PAYLOAD_BASE >> 16), lsl #16

_PayloadLoop:
  ldp   x2, x3, [x4], #16
  stp   x2, x3, [x5], #16
  subs  x6, x6, #16
  b.ne  _PayloadLoop

_NoPayload:
#endif
  ldr   x5, =FD_BASE

  /* songyuan diagnostic: paint the LK splash so the screen shows the shim ran */
_ShimPaint:
  ldr   x9, =0xFC800000
  movz  w10, #0xFF00
  movk  w10, #0xFF00, lsl #16
  add   x11, x9, #0x100000
1:
  str   w10, [x9], #4
  cmp   x9, x11
  b.ne  1b
  b     _Entry

_Entry:
  br    x5
#endif

_Dead:
  b     _Dead

.text
.align 4
#if defined(PAYLOAD_BASE) && defined(PAYLOAD_CAP)
  .ltorg   /* materialise literal pools here: the blob starts the FD image at _Payload */
  .align 4 /* keep the shim length a multiple of 16: mkxnuimg derives the container padding from it */
#endif

_Payload:
