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
    for (uint32_t offset = 0; offset <= 5000; offset += 100)
    {
        FallDetectorEvent event = reading(detector, start + offset, 0, 0, 1.5);
        assert(event == (offset == 5000 ? FALL_EVENT_READY : FALL_EVENT_NONE));
        assert(detector->state == (offset == 5000 ? FALL_STATE_NORMAL : FALL_STATE_WARMUP));
    }
    return start + 5000;
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
        assert(reading(detector, began + elapsed, accel, gyro, magnitude) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
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
    for (uint32_t tick = 0; tick < 5000; tick += 100)
        assert(reading(&detector, tick, 20, 1000, 50) == FALL_EVENT_NONE);
    assert(reading(&detector, 5000, 20, 1000, 50) == FALL_EVENT_READY);
    assert(reading(&detector, 5100, 20, 1000, 50) == FALL_EVENT_SPIKE);
}

static void deadline_stays_at_first_crossing_and_boundary_sample_is_excluded(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 100; elapsed < 8000; elapsed += 100)
    {
        /* A second sharp movement during settling must not restart the clock. */
        double accel = elapsed == 4000 ? 30 : 0;
        assert(reading(&detector, began + elapsed, accel, 0, 1.5) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
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
                       moving ? 12 : 1.5) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
    }
    assert(reading(&detector, began + 8000, 0, 0, 1.5) == FALL_EVENT_UNCERTAIN);
    assert(detector.state == FALL_STATE_UNCERTAIN);
    for (uint32_t elapsed = 8100; elapsed < 11000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
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
        assert(reading(&detector, began + elapsed, 6, 10, 15) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
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
    for (uint32_t elapsed = 100; elapsed < 5000; elapsed += 100)
        assert(reading(&detector, restarted + elapsed, 10, 10, 15) == FALL_EVENT_NONE);
    assert(reading(&detector, restarted + 5000, 10, 10, 15) == FALL_EVENT_READY);
    assert(reading(&detector, restarted + 5100, 10, 10, 15) == FALL_EVENT_SPIKE);
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
    for (uint32_t tick = 100; tick < 5000; tick += 100)
        assert(reading(&detector, tick, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(reading(&detector, 5000, 0, 0, 1.5) == FALL_EVENT_READY);

    missing.time_ms = 5100;
    assert(FallDetector_Update(&detector, &missing) == FALL_EVENT_RESTARTED);
    assert(detector.state == FALL_STATE_WARMUP);
    for (uint32_t tick = 5200; tick < 6000; tick += 100)
        assert(reading(&detector, tick, 0, 0, 1.5) == FALL_EVENT_NONE);
    missing.time_ms = 6000;
    assert(FallDetector_Update(&detector, &missing) != FALL_EVENT_READY);
    for (uint32_t tick = 6100; tick < 11000; tick += 100)
        assert(reading(&detector, tick, 0, 0, 1.5) == FALL_EVENT_NONE);
    assert(reading(&detector, 11000, 0, 0, 1.5) == FALL_EVENT_READY);
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
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
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
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
    for (uint32_t n = 0; n < 9; n++)
        assert(reading(&detector, began + 5150 + 700 * n / 8, 0, 0, 1.5)
               == FALL_EVENT_NONE);
    for (uint32_t elapsed = 6000; elapsed < 8000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 0, 0, 1.5) ==
               (elapsed == 2000 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_NONE));
    assert(reading(&detector, began + 8000, 0, 0, 1.5) == FALL_EVENT_UNCERTAIN);
}

static void unsigned_tick_wrap_preserves_elapsed_time(void)
{
    FallDetector detector;
    /* Independently cross wrap during warmup, the event gate and quiet blocks. */
    uint32_t began = trigger(&detector, UINT32_MAX - 1000U);
    assert(observe_constant(&detector, began, 0, 0, 1.5) == FALL_EVENT_FALL);
    began = trigger(&detector, UINT32_MAX - 6500U);
    assert(observe_constant(&detector, began, 0.02, 4, 12) == FALL_EVENT_NEAR_FALL);
    began = trigger(&detector, UINT32_MAX - 11000U);
    assert(observe_constant(&detector, began, 0, 0, 1.5) == FALL_EVENT_FALL);
    assert(strcmp(FallDetector_StateName(FALL_STATE_NORMAL), "") != 0);
}

static uint32_t ready_with_baseline(FallDetector *detector, double baseline)
{
    FallDetector_Init(detector);
    for (uint32_t time = 0; time <= 5000; time += 100)
        assert(reading(detector, time, baseline, 2, 10) ==
               (time == 5000 ? FALL_EVENT_READY : FALL_EVENT_NONE));
    return 5100;
}

static FallDetectorEvent gate(FallDetector *detector, uint32_t began,
                             double value, double boundary_value)
{
    assert(reading(detector, began, value, 2, 10) == FALL_EVENT_SPIKE);
    for (uint32_t elapsed = 100; elapsed < 2000; elapsed += 100)
        assert(reading(detector, began + elapsed, value, 2, 10) == FALL_EVENT_NONE);
    return reading(detector, began + 2000, boundary_value, 2, 10);
}

static void relative_disturbance_boundary_and_trigger_exclusion(void)
{
    FallDetector detector;
    uint32_t began = ready_with_baseline(&detector, 2);
    /* The trigger belongs to the event, and the +2s sample does not. */
    assert(gate(&detector, began, 8, 1000) == FALL_EVENT_DISTURBANCE_CONFIRMED);
    assert(detector.candidate_baseline_mean == 2);
    assert(detector.event_accel_msd_mean == 8);
    assert(detector.disturbance_ratio == 4);
    assert(detector.state == FALL_STATE_OBSERVING);

    began = ready_with_baseline(&detector, 2);
    assert(gate(&detector, began, 7.999, 1000) == FALL_EVENT_DISTURBANCE_REJECTED);
    assert(detector.state == FALL_STATE_NORMAL);
    assert(!detector.fall_latched);
    assert(reading(&detector, began + 2100, 8, 2, 10) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 2200, 4.9, 2, 10) == FALL_EVENT_NONE);
    assert(reading(&detector, began + 2300, 8, 2, 10) == FALL_EVENT_SPIKE);
}

static void sustained_high_motion_is_rejected_not_a_near_fall(void)
{
    FallDetector detector;
    uint32_t began = ready_with_baseline(&detector, 8);
    assert(gate(&detector, began, 8, 8) == FALL_EVENT_DISTURBANCE_REJECTED);
    assert(detector.disturbance_ratio == 1);
    for (uint32_t elapsed = 2100; elapsed < 12000; elapsed += 100)
        assert(reading(&detector, began + elapsed, 8, 1000, 50) == FALL_EVENT_NONE);
    assert(detector.state == FALL_STATE_NORMAL && !detector.fall_latched);
}

static void noise_floor_and_first_trigger_sample_contribute(void)
{
    const double baselines[] = {0, 0.001, 0.01};
    for (unsigned index = 0; index < 3; index++)
    {
        FallDetector detector;
        uint32_t began = ready_with_baseline(&detector, baselines[index]);
        assert(reading(&detector, began, 5, 0, 1.5) == FALL_EVENT_SPIKE);
        for (uint32_t elapsed = 100; elapsed < 2000; elapsed += 100)
            assert(reading(&detector, began + elapsed, 0, 0, 1.5) == FALL_EVENT_NONE);
        assert(reading(&detector, began + 2000, 0, 0, 1.5) == FALL_EVENT_DISTURBANCE_CONFIRMED);
        assert(fabs(detector.event_accel_msd_mean - 0.25) < 1e-12);
        assert(fabs(detector.disturbance_ratio - 25) < 1e-10);
        assert(fabs(detector.candidate_baseline_mean - baselines[index]) < 1e-12);
    }
}

static void settling_is_excluded_and_baseline_rolls_forward(void)
{
    FallDetector detector;
    FallDetector_Init(&detector);
    for (uint32_t tick = 0; tick <= 5000; tick += 100)
        assert(reading(&detector, tick, tick < 2000 ? 100 : 0.1, 0, 1.5) ==
               (tick == 5000 ? FALL_EVENT_READY : FALL_EVENT_NONE));
    assert(reading(&detector, 5100, 5, 0, 1.5) == FALL_EVENT_SPIKE);
    assert(fabs(detector.candidate_baseline_mean - 0.1) < 1e-12);

    (void)ready_with_baseline(&detector, 0);
    /* Enough normal observations to overwrite a 64-entry ring twice. */
    for (uint32_t tick = 5100; tick <= 20000; tick += 100)
        assert(reading(&detector, tick, 3, 2, 10) == FALL_EVENT_NONE);
    assert(gate(&detector, 20100, 12, 0) == FALL_EVENT_DISTURBANCE_CONFIRMED);
    assert(detector.candidate_baseline_mean == 3);
    assert(detector.disturbance_ratio == 4);
}

static void inadequate_event_coverage_is_unknown_and_rewarms(void)
{
    for (unsigned kind = 0; kind < 3; kind++)
    {
        FallDetector detector;
        uint32_t began = trigger(&detector, 0);
        for (uint32_t elapsed = 100; elapsed < 2000; elapsed += 100)
        {
            /* Gaps remain <=250ms: the gate itself must reject the coverage. */
            if (kind == 0 && (elapsed == 300 || elapsed == 700 || elapsed == 1100)) continue;
            if (kind == 1 && elapsed == 1900) continue;
            if (kind == 2 && (elapsed == 300 || elapsed == 700)) continue;
            assert(reading(&detector, began + elapsed, 1, 2, 10) == FALL_EVENT_NONE);
        }
        FallDetectorEvent event = reading(&detector, began + 2000, 0, 0, 1.5);
        /* Exactly18 samples are adequate; 17 or an early final sample is not. */
        assert(event == (kind == 2 ? FALL_EVENT_DISTURBANCE_CONFIRMED : FALL_EVENT_DISTURBANCE_UNKNOWN));
        assert(detector.state == (kind == 2 ? FALL_STATE_OBSERVING : FALL_STATE_WARMUP));
        assert(!detector.fall_latched);
    }
}

static void duplicated_timestamps_cannot_supply_event_evidence(void)
{
    FallDetector detector;
    uint32_t began = trigger(&detector, 0);
    for (uint32_t elapsed = 200; elapsed < 2000; elapsed += 200)
    {
        assert(reading(&detector, began + elapsed, 1, 2, 10) == FALL_EVENT_NONE);
        for (unsigned duplicate = 0; duplicate < 10; duplicate++)
            assert(reading(&detector, began + elapsed, 100, 2, 10) == FALL_EVENT_NONE);
    }
    assert(reading(&detector, began + 2000, 0, 0, 1.5) == FALL_EVENT_DISTURBANCE_UNKNOWN);
    assert(detector.state == FALL_STATE_WARMUP);
}

static bool baseline_coverage(unsigned count, uint32_t oldest, uint32_t newest)
{
    FallDetector detector;
    FallDetector_Init(&detector);
    /* This window crosses UINT32 rollover and carries a constant known mean. */
    const uint32_t now = 1000;
    for (unsigned index = 0; index < count; index++)
    {
        uint32_t age = oldest - (oldest - newest) * index / (count - 1);
        FallDetectorInput input = {.time_ms = now - age, .accel_msd = 2};
        FallDetector_AddBaseline(&detector, &input);
    }
    double mean;
    bool valid = FallDetector_Baseline(&detector, now, &mean);
    assert(mean == 2);
    return valid;
}

static void baseline_coverage_boundaries_and_unknown_history(void)
{
    assert(baseline_coverage(28, 2850, 100)); /* Minimum count, age and span. */
    assert(baseline_coverage(28, 3000, 150)); /* Old/new endpoints inclusive. */
    assert(!baseline_coverage(27, 3000, 100));
    assert(!baseline_coverage(28, 2849, 99));
    assert(!baseline_coverage(28, 3000, 151));
    assert(!baseline_coverage(28, 2850, 101));

    FallDetector detector;
    (void)ready_with_baseline(&detector, 2);
    /* A150ms interval remains covered, while151ms lacks recent baseline. */
    assert(reading(&detector, 5150, 8, 2, 10) == FALL_EVENT_SPIKE);
    (void)ready_with_baseline(&detector, 2);
    assert(reading(&detector, 5151, 8, 2, 10) == FALL_EVENT_DISTURBANCE_UNKNOWN);
    assert(detector.state == FALL_STATE_WARMUP && !detector.fall_latched);
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
    relative_disturbance_boundary_and_trigger_exclusion();
    sustained_high_motion_is_rejected_not_a_near_fall();
    noise_floor_and_first_trigger_sample_contribute();
    settling_is_excluded_and_baseline_rolls_forward();
    inadequate_event_coverage_is_unknown_and_rewarms();
    duplicated_timestamps_cannot_supply_event_evidence();
    baseline_coverage_boundaries_and_unknown_history();
    puts("Fall detector: 17 test groups passed.");
    return 0;
}
