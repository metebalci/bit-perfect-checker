// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Test builds only (CMake option BPC_TEST_INJECT, never in a release): late
// re-arms of the audio OUT endpoint on purpose, to test the hole counter and
// TinyUSB's re-arm rule. The link wraps TinyUSB's dcd_edpt_xfer()
// (-Wl,--wrap=dcd_edpt_xfer).
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

#ifndef INJECT_H
#define INJECT_H

// From the console: the command's argument (NULL without one).
void inject_command(const char *arg);

#endif
