// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host stand-in for TinyUSB's tusb.h, for the host tool stream_tool.c: the
// receive FIFO (TinyUSB's own tu_fifo) and the calls app/stream.c makes. The
// tool's build passes CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ from
// app/tusb_config.h.

#ifndef HOST_TUSB_H
#define HOST_TUSB_H

#include <stdbool.h>
#include <stddef.h>

// osal.h first: it includes tusb_fifo.h before osal_none.h needs it
#include "osal/osal.h"
#include "common/tusb_fifo.h"

#ifndef CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ
#error "CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ: pass the value of app/tusb_config.h"
#endif

tu_fifo_t *tud_audio_get_ep_out_ff(void);
bool tud_mounted(void);

#endif
