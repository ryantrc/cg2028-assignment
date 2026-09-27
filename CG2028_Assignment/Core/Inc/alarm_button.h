#ifndef ALARM_BUTTON_H
#define ALARM_BUTTON_H

#include <stdbool.h>
#include <stdint.h>

#define ALARM_BUTTON_DEBOUNCE_MS 40U
#define ALARM_BUTTON_HOLD_MS 2000U

typedef struct {
    bool raw_pressed;
    bool debounced_pressed;
    bool armed;
    bool consumed;
    bool hold_eligible;
    uint32_t raw_changed_ms;
    uint32_t pressed_ms;
} AlarmButton;

static inline void AlarmButton_Init(AlarmButton *button, bool raw_pressed, uint32_t now_ms)
{
    *button = (AlarmButton){0};
    button->raw_pressed = raw_pressed;
    button->raw_changed_ms = now_ms;
    button->armed = !raw_pressed;
}

/* Call on every loop, including between the 100 ms sensor acquisitions. */
static inline bool AlarmButton_Update(AlarmButton *button, bool raw_pressed,
                                      bool alarm_active, uint32_t now_ms)
{
    if (raw_pressed != button->raw_pressed) {
        button->raw_pressed = raw_pressed;
        button->raw_changed_ms = now_ms;
    }
    if (raw_pressed != button->debounced_pressed &&
        (uint32_t)(now_ms - button->raw_changed_ms) >= ALARM_BUTTON_DEBOUNCE_MS) {
        button->debounced_pressed = raw_pressed;
        if (raw_pressed) {
            button->pressed_ms = now_ms;
            button->hold_eligible = button->armed && alarm_active;
        } else {
            button->armed = true;
            button->consumed = false;
            button->hold_eligible = false;
        }
    }
    if (!alarm_active) button->hold_eligible = false;
    if (button->debounced_pressed && button->hold_eligible && !button->consumed &&
        (uint32_t)(now_ms - button->pressed_ms) >= ALARM_BUTTON_HOLD_MS) {
        button->consumed = true;
        button->armed = false;
        return true;
    }
    return false;
}

#endif
