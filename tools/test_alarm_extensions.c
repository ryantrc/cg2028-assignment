#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "alarm_button.h"
#include "fall_detector.h"

static FallDetectorEvent sample(FallDetector *d, uint32_t t, double a, double g, double m)
{
    FallDetectorInput input = {.time_ms=t, .valid=true, .msd_valid=true,
                               .accel_msd=a, .gyro_msd=g, .gyro_magnitude=m};
    return FallDetector_Update(d, &input);
}

static void button_cases(void)
{
    AlarmButton b;
    AlarmButton_Init(&b, false, 0);
    assert(!AlarmButton_Update(&b, true, true, 100));
    assert(!AlarmButton_Update(&b, false, true, 110));
    assert(!AlarmButton_Update(&b, true, true, 120));
    assert(!AlarmButton_Update(&b, true, true, 159));
    assert(!AlarmButton_Update(&b, true, true, 160));
    assert(!AlarmButton_Update(&b, true, true, 2159));
    assert(AlarmButton_Update(&b, true, true, 2160));
    assert(!AlarmButton_Update(&b, true, true, 5000));
    assert(!AlarmButton_Update(&b, false, false, 5010));
    assert(!AlarmButton_Update(&b, false, false, 5050));
    assert(!AlarmButton_Update(&b, true, true, 5100));
    assert(!AlarmButton_Update(&b, true, true, 5140));
    assert(!AlarmButton_Update(&b, false, true, 6000));
    assert(!AlarmButton_Update(&b, false, true, 6040));
    AlarmButton_Init(&b, false, 0);
    assert(!AlarmButton_Update(&b, true, false, 100));
    assert(!AlarmButton_Update(&b, true, false, 140));
    assert(!AlarmButton_Update(&b, true, true, 3000)); /* Held before fall. */
    assert(!AlarmButton_Update(&b, false, true, 3010));
    assert(!AlarmButton_Update(&b, false, true, 3050));
    AlarmButton_Init(&b, false, UINT32_MAX - 100U);
    assert(!AlarmButton_Update(&b, true, true, UINT32_MAX - 50U));
    assert(!AlarmButton_Update(&b, true, true, UINT32_MAX - 10U));
    assert(AlarmButton_Update(&b, true, true, 1989U));
}

static void long_lie_cases(void)
{
    FallDetector d;
    FallDetector_Init(&d);
    d.fall_latched = true;
    d.state = FALL_STATE_FALL_LATCHED;
    d.block_start_ms = 1000;
    for (uint32_t t = 1100; t <= 31000; t += 100) {
        FallDetectorEvent event = sample(&d, t, 0.001, 0.1, 1);
        assert(event == (t == 31000 ? FALL_EVENT_LONG_LIE : FALL_EVENT_NONE));
    }
    assert(d.state == FALL_STATE_LONG_LIE && d.fall_latched);
    assert(sample(&d, 31100, 10, 10, 10) == FALL_EVENT_NONE);
    FallDetectorInput fault = {.time_ms=31200, .valid=false};
    assert(FallDetector_Update(&d, &fault) == FALL_EVENT_SENSOR_FAULT);
    assert(d.state == FALL_STATE_LONG_LIE && d.fall_latched);
    FallDetector_ManualReset(&d);
    assert(d.state == FALL_STATE_WARMUP && !d.fall_latched);
    assert(sample(&d, 31300, 0.001, 0.1, 1) == FALL_EVENT_NONE);
    assert(d.state == FALL_STATE_WARMUP);

    FallDetector_Init(&d);
    d.fall_latched = true; d.state = FALL_STATE_FALL_LATCHED; d.block_start_ms = 0;
    for (uint32_t t = 100; t <= 29000; t += 100) {
        double a = (t >= 15000 && t < 16000) ? 1 : 0.001;
        (void)sample(&d, t, a, 0.1, 1);
    }
    assert(d.state == FALL_STATE_FALL_LATCHED);
    assert(d.long_lie_quiet_blocks < 30);
    FallDetectorInput bad = {.time_ms=29100, .valid=false};
    (void)FallDetector_Update(&d, &bad);
    assert(d.long_lie_quiet_blocks == 0);

    FallDetector_Init(&d);
    d.fall_latched = true; d.state = FALL_STATE_FALL_LATCHED; d.block_start_ms = 0;
    for (uint32_t t = 100; t <= 900; t += 100)
        (void)sample(&d, t, 0.001, 0.1, 1);
    (void)sample(&d, 900, 0.001, 0.1, 1); /* Duplicate breaks coverage. */
    (void)sample(&d, 1000, 0.001, 0.1, 1);
    assert(d.long_lie_quiet_blocks == 0);
    (void)sample(&d, 1300, 0.001, 0.1, 1); /* Sparse gap breaks streak. */
    assert(d.long_lie_quiet_blocks == 0);
}

int main(void)
{
    button_cases();
    long_lie_cases();
    puts("Alarm extension tests passed.");
}
