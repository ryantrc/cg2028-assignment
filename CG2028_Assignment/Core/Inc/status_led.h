#ifndef STATUS_LED_H
#define STATUS_LED_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    STATUS_LED_SETUP,
    STATUS_LED_NORMAL,
    STATUS_LED_FALL
} StatusLedMode;

/* One LED: steady during setup, one-second on/off in normal operation,
 * and 30 complete on/off cycles per second when a fall is detected.
 * Integer millisecond ticks distribute 33/34 ms periods across each second. */
static inline bool StatusLed_IsOn(StatusLedMode mode, uint32_t now_ms)
{
    if (mode == STATUS_LED_SETUP) return true;
    if (mode == STATUS_LED_NORMAL) return (now_ms % 2000u) < 1000u;
    return ((((now_ms % 1000u) * 60u) / 1000u) & 1u) == 0u;
}

#endif
