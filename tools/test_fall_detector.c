/* Native checks against the same header included by the board firmware.
 * cc -std=c11 -Wall -Wextra -Werror -pedantic \
 *   -ICG2028_Assignment/Core/Inc tools/test_fall_detector.c -lm -o /tmp/test_fall_detector
 */
#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fall_detector.h"

static FallDetectorEvent reading(FallDetector *detector, uint32_t time_ms,
                                double accel_msd, double gyro_msd,
                                double gyro_magnitude)
{
    const FallDetectorInput input = {
        .time_ms = time_ms, .valid = true, .msd_valid = true,
        .accel_msd = accel_msd, .gyro_msd = gyro_msd,
        .gyro_magnitude = gyro_magnitude
    };
    return FallDetector_Update(detector, &input);
}

static uint32_t ready(FallDetector *detector, uint32_t start)
{
    FallDetector_Init(detector);
    assert(detector->state == FALL_STATE_WARMUP);
    assert(!detector->fall_latched);
    for (uint32_t offset = 0; offset <= 2000; offset += 100)
    {
        FallDetectorEvent event = reading(detector, start + offset, 0, 0, 1.5);
        assert(event == (offset == 2000 ? FALL_EVENT_READY : FALL_EVENT_NONE));
        assert(detector->state == (offset == 2000 ? FALL_STATE_NORMAL : FALL_STATE_WARMUP));
    }
    return start + 2000;
}

static uint32_t trigger(FallDetector *detector, uint32_t start)
{
    uint32_t time = ready(detector, start) + 100;
    /* A rotational peak is not required for this acceleration-only trigger. */
    assert(reading(detector, time, 5, 0, 1.5) == FALL_EVENT_SPIKE);
    assert(detector->state == FALL_STATE_OBSERVING);
    return time;
}

static FallDetectorEvent observe_constant(FallDetector *detector, uint32_t began,
                                         double accel, double gyro, double magnitude)
{
    for (uint32_t elapsed = 100; elapsed < 8000; elapsed += 100)
        assert(reading(detector, began + elapsed, accel, gyro, magnitude) == FALL_EVENT_NONE);
    return reading(detector, began + 8000, accel, gyro, magnitude);
}

static void warmup_and_trigger_threshold(void)
{
    FallDetector detector;
    uint32_t time = ready(&detector, 0);
    assert(reading(&detector, time + 100, nextafter(5.0, 0.0), 5000, 100) == FALL_EVENT_NONE);
    assert(reading(&detector, time + 200, 5.0, 0, 0) == FALL_EVENT_SPIKE);

    /* Movement during warmup cannot trigger, including the READY sample. */
    FallDetector_Init(&detector);
    for (uint32_t tick = 0; tick < 2000; tick += 100)
        assert(reading(&detector, tick, 20, 1000, 50) == FALL_EVENT_NONE);
    assert(reading(&detector, 2000, 20, 1000, 50) == FALL_EVENT_READY);
    assert(reading(&detector, 2100, 20, 1000, 50) == FALL_EVENT_SPIKE);
}

static void deadline_stays_at_first_crossing_and_boundary_sample_is_excluded(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 100; elapsed < 8000; elapsed += 100)
    {
        /* A second sharp movement during settling must not restart the clock. */
        double accel = elapsed == 4000 ? 30 : 0;
        assert(reading(&detector, began + elapsed, accel, 0, 1.5) == FALL_EVENT_NONE);
    }
    /* This sample belongs to the next block, not the preceding 7--8 s block. */
    assert(reading(&detector, began + 8000, 50, 5000, 200) == FALL_EVENT_FALL);
    assert(detector.state == FALL_STATE_FALL_LATCHED);
    assert(detector.fall_latched);
}

static void quiet_moving_and_exact_metric_boundaries(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    assert(observe_constant(&detector, began, 0.004, 0.9, 4.9) == FALL_EVENT_FALL);

    began = trigger(&detector, 0);
    assert(observe_constant(&detector, began, 0.005, 1, 5) == FALL_EVENT_NEAR_FALL);
    assert(detector.state == FALL_STATE_NORMAL);
    assert(!detector.fall_latched);

    /* Each metric matters independently: mixed evidence is not recovery. */
    const double mixed[3][3] = {{0.005, 0, 1.5}, {0, 1, 1.5}, {0, 0, 5}};
    for (unsigned i = 0; i < 3; i++)
    {
        began = trigger(&detector, 0);
        assert(observe_constant(&detector, began, mixed[i][0], mixed[i][1], mixed[i][2])
               == FALL_EVENT_UNCERTAIN);
        assert(detector.state == FALL_STATE_UNCERTAIN);
        assert(!detector.fall_latched);
    }
}

static void uncertain_requires_three_consecutive_agreeing_blocks(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 100; elapsed < 8000; elapsed += 100)
    {
        bool moving = elapsed >= 6000 && elapsed < 7000;
        bool mixed = elapsed >= 7000;
        assert(reading(&detector, began + elapsed,
                       moving ? 0.02 : 0, moving || mixed ? 4 : 0,
                       moving ? 12 : 1.5) == FALL_EVENT_NONE);
    }
    assert(reading(&detector, began + 8000, 0, 0, 1.5) == FALL_EVENT_UNCERTAIN);
    assert(detector.state == FALL_STATE_UNCERTAIN);
    for (uint32_t elapsed = 8100; elapsed < 11000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 11000, 0, 0, 1.5) == FALL_EVENT_FALL);

    /* Uncertainty can resolve to movement as well as stillness. */
    began = trigger(&detector, 0);
    assert(observe_constant(&detector, began, 0, 3, 1.5) == FALL_EVENT_UNCERTAIN);
    for (uint32_t elapsed = 8100; elapsed < 11000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0.02, 4, 12) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 11000, 0.02, 4, 12) == FALL_EVENT_NEAR_FALL);
    assert(detector.state == FALL_STATE_NORMAL);
}

static void near_fall_requires_trigger_release_before_rearming(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 100; elapsed < 8000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 6, 10, 15) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 8000, 6, 10, 15) == FALL_EVENT_NEAR_FALL);
    assert(reading(&detector, began + 8100, 6, 10, 15) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 8200, 4.9, 10, 15) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 8300, 5, 10, 15) == FALL_EVENT_SPIKE);
}

static void sample_gaps_cancel_observation_and_restart_warmup(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    assert(reading(&detector, began + 250, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(detector.state == FALL_STATE_OBSERVING);
    uint32_t restarted = began + 501;
    assert(reading(&detector, restarted, 0, 0, 1.5) == FALL_EVENT_RESTARTED);
    assert(detector.state == FALL_STATE_WARMUP);
    assert(!detector.fall_latched);
    for (uint32_t elapsed = 100; elapsed < 2000; elapsed += 100)
        assert(reading(&detector, restarted + elapsed, 10, 10, 15) == FALL_EVENT_NONE);
    assert(reading(&detector, restarted + 2000, 10, 10, 15) == FALL_EVENT_READY);
    assert(reading(&detector, restarted + 2100, 10, 10, 15) == FALL_EVENT_SPIKE);
}

static void missing_msd_requires_fresh_history(void)
{
    FallDetector detector;
    FallDetector_Init(&detector);
    FallDetectorInput missing = {
        .time_ms = 0, .valid = true, .msd_valid = false,
        .accel_msd = NAN, .gyro_msd = NAN, .gyro_magnitude = 1.5
    };
    assert(FallDetector_Update(&detector, &missing) == FALL_EVENT_NONE);
    for (uint32_t tick = 100; tick < 2000; tick += 100)
        assert(reading(&detector, tick, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(reading(&detector, 2000, 0, 0, 1.5) == FALL_EVENT_READY);

    missing.time_ms = 2100;
    assert(FallDetector_Update(&detector, &missing) == FALL_EVENT_RESTARTED);
    assert(detector.state == FALL_STATE_WARMUP);
    for (uint32_t tick = 2200; tick < 3000; tick += 100)
        assert(reading(&detector, tick, 0, 0, 1.5) == FALL_EVENT_NONE);
    missing.time_ms = 3000;
    assert(FallDetector_Update(&detector, &missing) != FALL_EVENT_READY);
    for (uint32_t tick = 3100; tick < 5000; tick += 100)
        assert(reading(&detector, tick, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(reading(&detector, 5000, 0, 0, 1.5) == FALL_EVENT_READY);
}

static void faults_and_latch_survive_recovery_and_gaps(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    FallDetectorInput bad = {
        .time_ms = began + 100, .valid = false, .msd_valid = true,
        .accel_msd = 0, .gyro_msd = 0, .gyro_magnitude = 0
    };
    assert(FallDetector_Update(&detector, &bad) == FALL_EVENT_SENSOR_FAULT);
    assert(detector.state == FALL_STATE_SENSOR_FAULT);
    bad.time_ms += 100;
    assert(FallDetector_Update(&detector, &bad) == FALL_EVENT_NONE);
    assert(reading(&detector, bad.time_ms + 100, 0, 0, 1.5) == FALL_EVENT_RESTARTED);
    assert(detector.state == FALL_STATE_WARMUP);

    const double invalid[3][3] = {{NAN, 0, 1.5}, {0, -1, 1.5}, {0, 0, INFINITY}};
    for (unsigned i = 0; i < 3; i++)
    {
        uint32_t now = ready(&detector, 0) + 100;
        assert(reading(&detector, now, invalid[i][0], invalid[i][1], invalid[i][2])
               == FALL_EVENT_SENSOR_FAULT);
        assert(detector.state == FALL_STATE_SENSOR_FAULT);
    }

    began = trigger(&detector, 0);
    assert(observe_constant(&detector, began, 0, 0, 1.5) == FALL_EVENT_FALL);
    bad.time_ms = began + 8100;
    assert(FallDetector_Update(&detector, &bad) == FALL_EVENT_SENSOR_FAULT);
    assert(detector.fall_latched && detector.state == FALL_STATE_FALL_LATCHED);
    (void)reading(&detector, began + 8200, 0.1, 10, 20);
    assert(detector.fall_latched && detector.state == FALL_STATE_FALL_LATCHED);
    (void)reading(&detector, began + 20000, 0.1, 10, 20);
    assert(detector.fall_latched && detector.state == FALL_STATE_FALL_LATCHED);
    FallDetector_Init(&detector);
    assert(!detector.fall_latched && detector.state == FALL_STATE_WARMUP);
}

static FallDetectorEvent sparse_observation(unsigned kind)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 100; elapsed < 5000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) == FALL_EVENT_NONE);
    if (kind == 3)
        assert(reading(&detector, began + 4950, 0, 0, 1.5) == FALL_EVENT_NONE);
    for (uint32_t block = 0; block < 3; block++)
    {
        for (uint32_t n = 0; n < 10; n++)
        {
            uint32_t offset = n * 100;
            if (kind == 0 && n == 4) continue;           /* Nine well-spread samples. */
            if (kind == 1 && (n == 3 || n == 7)) continue; /* Only eight samples. */
            if (kind == 2 && n == 9) continue;           /* Last sample too early. */
            if (kind == 3) offset = 151 + n * 88;        /* First sample too late. */
            assert(reading(&detector, began + 5000 + block * 1000 + offset, 0, 0, 1.5)
                   == FALL_EVENT_NONE);
        }
    }
    return reading(&detector, began + 8000, 0, 0, 1.5);
}

static void incomplete_blocks_cannot_prove_stillness(void)
{
    assert(sparse_observation(0) == FALL_EVENT_FALL);
    assert(sparse_observation(1) == FALL_EVENT_UNCERTAIN);
    assert(sparse_observation(2) == FALL_EVENT_UNCERTAIN);
    assert(sparse_observation(3) == FALL_EVENT_UNCERTAIN);

    /* Nine samples can satisfy both end limits yet span only 700 ms. */
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 100; elapsed < 5000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) == FALL_EVENT_NONE);
    for (uint32_t n = 0; n < 9; n++)
        assert(reading(&detector, began + 5150 + 700 * n / 8, 0, 0, 1.5)
               == FALL_EVENT_NONE);
    for (uint32_t elapsed = 6000; elapsed < 8000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 8000, 0, 0, 1.5) == FALL_EVENT_UNCERTAIN);
}

static void unsigned_tick_wrap_preserves_elapsed_time(void)
{
    FallDetector detector;
    /* Warmup crosses wrap; the later observation crosses another chosen wrap. */
    uint32_t began = trigger(&detector, UINT32_MAX - 1000U);
    assert(observe_constant(&detector, began, 0, 0, 1.5) == FALL_EVENT_FALL);
    began = trigger(&detector, UINT32_MAX - 5000U);
    assert(observe_constant(&detector, began, 0.02, 4, 12) == FALL_EVENT_NEAR_FALL);
    assert(strcmp(FallDetector_StateName(FALL_STATE_NORMAL), "") != 0);
}

int main(void)
{
    warmup_and_trigger_threshold();
    deadline_stays_at_first_crossing_and_boundary_sample_is_excluded();
    quiet_moving_and_exact_metric_boundaries();
    uncertain_requires_three_consecutive_agreeing_blocks();
    near_fall_requires_trigger_release_before_rearming();
    sample_gaps_cancel_observation_and_restart_warmup();
    missing_msd_requires_fresh_history();
    faults_and_latch_survive_recovery_and_gaps();
    incomplete_blocks_cannot_prove_stillness();
    unsigned_tick_wrap_preserves_elapsed_time();
    puts("Fall detector: 10 test groups passed.");
    return 0;
}
