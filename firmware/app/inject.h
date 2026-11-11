// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Test builds only (CMake option BPC_TEST_INJECT, never in a release): late
// re-arms of the audio OUT endpoint on purpose, to test the hole counter and
// u2ps's re-arm rule, on u2ps's injection hook (U2PS_TEST_INJECT,
// u2ps_dwc2_inject()); and the time u2ps's handler takes per kind of pass.
//
//   inject <every> <us>  every <every>th re-arm from the USB interrupt that
//                        would be in time (the interrupt entered in the
//                        packet's microframe, which still runs) waits until
//                        the next microframe starts, then <us> microseconds
//                        (0 to 1000), then re-arms; it answers with the
//                        inject line below, counts cleared
//   inject off           no more injections
//   inject               "inject every=<n> us=<n> injected=<n> skipped=<n>
//                        timeouts=<n> d_min_ns=<n> d_max_ns=<n>": skipped
//                        counts chosen re-arms that were late already (the
//                        next one is taken instead), timeouts the waits that
//                        saw no new microframe within two, d the time from the
//                        new microframe to the end of the re-arm
//   inject passes [clear] u2ps's handler time by kind of pass, one line per
//                        kind: "passes kind=<kind> n=<n> max=<cycles>
//                        mean=<cycles> sum_k=<kilocycles>"; clear starts
//                        the counts over after the lines

#ifndef INJECT_H
#define INJECT_H

// From the console: the command's argument (NULL without one).
void inject_command(const char *arg);

#endif
