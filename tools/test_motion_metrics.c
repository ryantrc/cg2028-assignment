#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "motion_metrics.h"

static void close_to(double actual, double expected)
{
    assert(isfinite(actual));
    assert(fabs(actual - expected) <= 1e-10 * (1.0 + fabs(expected)));
}

static MotionMetrics equal_axes(MotionMetricsState *state, float value, uint32_t time_ms)
{
    const float xyz[3] = {value, value, value};
    return MotionMetrics_Update(state, xyz, time_ms);
}

static MotionMetrics on_x_axis(MotionMetricsState *state, float value, uint32_t time_ms)
{
    const float xyz[3] = {value, 0, 0};
    return MotionMetrics_Update(state, xyz, time_ms);
}

static void startup_requires_complete_windows(void)
{
    MotionMetricsState state = {0};
    assert(MOTION_SLOPE_WINDOW_SAMPLES == 5);
    for (unsigned i = 0; i < 6; i++)
    {
        MotionMetrics result = on_x_axis(&state, (float)i, 1000U + 100U * i);
        close_to(result.magnitude, i);
        assert(result.msd_valid == (i >= 1));
        assert(result.magnitude_slope_valid == (i >= 4));
        assert(result.msd_slope_valid == (i >= 5));
        if (result.msd_valid) close_to(result.msd, 1.0 / 3.0);
        if (result.magnitude_slope_valid) close_to(result.magnitude_slope, 10);
        if (result.msd_slope_valid) close_to(result.msd_slope, 0);
    }
}

static void signed_slopes_use_actual_irregular_timestamps(void)
{
    const uint32_t times[5] = {0, 100, 400, 500, 900};
    const float increasing[5] = {0, 1, 4, 5, 9};
    MotionMetricsState up = {0}, down = {0};
    MotionMetrics a = {0}, b = {0};
    for (unsigned i = 0; i < 5; i++)
    {
        a = on_x_axis(&up, increasing[i], times[i]);
        b = on_x_axis(&down, 100 - increasing[i], times[i]);
    }
    assert(a.magnitude_slope_valid && b.magnitude_slope_valid);
    close_to(a.magnitude_slope, 10);
    close_to(b.magnitude_slope, -10);
}

static void rolling_window_evicts_old_values(void)
{
    const float values[6] = {100, 0, 1, 2, 3, 4};
    MotionMetricsState state = {0};
    for (unsigned i = 0; i < 6; i++)
    {
        MotionMetrics result = on_x_axis(&state, values[i], 100U * i);
        if (i == 4) close_to(result.magnitude_slope, -192);
        if (i == 5) close_to(result.magnitude_slope, 10);
    }
    MotionMetrics last = {0};
    for (unsigned i = 6; i < 11; i++)
        last = on_x_axis(&state, 15.0f - (float)i, 100U * i);
    close_to(last.magnitude_slope, -10);
}

static void constant_and_symmetric_spikes_have_zero_slope(void)
{
    MotionMetricsState constant = {0}, magnitude_spike = {0}, msd_spike = {0};
    MotionMetrics result = {0};
    for (unsigned i = 0; i < 7; i++)
        result = equal_axes(&constant, 2, 100U * i);
    assert(result.magnitude_slope_valid && result.msd_slope_valid);
    close_to(result.msd, 0);
    close_to(result.magnitude_slope, 0);
    close_to(result.msd_slope, 0);

    const float spike[5] = {0, 0, 10, 0, 0};
    for (unsigned i = 0; i < 5; i++)
        result = on_x_axis(&magnitude_spike, spike[i], 100U * i);
    close_to(result.magnitude_slope, 0); /* Center spike has no linear trend. */

    const float step[6] = {0, 0, 0, 10, 10, 10};
    for (unsigned i = 0; i < 6; i++)
        result = equal_axes(&msd_spike, step[i], 100U * i);
    assert(result.msd_slope_valid);
    close_to(result.msd_slope, 0); /* Five MSD values are 0, 0, 100, 0, 0. */
}

static void msd_slopes_can_increase_or_decrease(void)
{
    MotionMetricsState up = {0}, down = {0}, irregular = {0};
    (void)equal_axes(&up, 0, 0);
    (void)equal_axes(&down, 0, 0);
    (void)equal_axes(&irregular, 0, 0);
    float increasing = 0, decreasing = 0;
    MotionMetrics a = {0}, b = {0}, c = {0};
    for (unsigned i = 1; i <= 5; i++)
    {
        increasing += (float)i;
        decreasing += (float)(6U - i);
        a = equal_axes(&up, increasing, 100U * i);
        b = equal_axes(&down, decreasing, 100U * i);
        c = equal_axes(&irregular, increasing, 100U * i * i);
    }
    assert(a.msd_slope_valid && b.msd_slope_valid && c.msd_slope_valid);
    close_to(a.msd_slope, 60); /* Regression of 1, 4, 9, 16, 25 at 100 ms. */
    close_to(b.msd_slope, -60);
    close_to(c.msd_slope, 10); /* Here MSD = time_ms / 100 exactly. */
}

static void magnitude_uses_all_axes_without_signed_cancellation(void)
{
    const float mixed_signs[3] = {3, -4, 12};
    MotionMetricsState state = {0};
    MotionMetrics result = MotionMetrics_Update(&state, mixed_signs, 0);
    close_to(result.magnitude, 13); /* sqrt(9 + 16 + 144), not signed Avg. */
    assert(!result.msd_valid && !result.magnitude_slope_valid);

    MotionMetricsState negative_axis = {0};
    for (unsigned i = 0; i < 5; i++)
        result = on_x_axis(&negative_axis, (float)i - 5.0f, 100U * i);
    close_to(result.magnitude, 1);
    close_to(result.magnitude_slope, -10); /* X rises; its magnitude falls. */
}

static void rotation_changes_axis_msd_but_not_constant_magnitude(void)
{
    const float directions[6][3] = {
        {1, 0, 0}, {0, 1, 0}, {-1, 0, 0},
        {0, -1, 0}, {1, 0, 0}, {0, 1, 0}
    };
    MotionMetricsState state = {0};
    MotionMetrics result = {0};
    for (unsigned i = 0; i < 6; i++)
    {
        result = MotionMetrics_Update(&state, directions[i], 100U * i);
        close_to(result.magnitude, 1);
        if (i) close_to(result.msd, 2.0 / 3.0);
        if (result.magnitude_slope_valid) close_to(result.magnitude_slope, 0);
    }
    assert(result.msd_slope_valid);
    close_to(result.msd_slope, 0);
}

static void tick_wrap_and_long_uptime_do_not_change_slopes(void)
{
    const uint32_t starts[2] = {UINT32_MAX - 249U, 4000000000U};
    for (unsigned j = 0; j < 2; j++)
    {
        MotionMetricsState state = {0};
        MotionMetrics result = {0};
        for (unsigned i = 0; i < 6; i++)
            result = on_x_axis(&state, (float)i, starts[j] + 100U * i);
        assert(result.magnitude_slope_valid && result.msd_slope_valid);
        close_to(result.magnitude_slope, 10);
        close_to(result.msd_slope, 0);
    }
}

static void zero_time_variance_is_invalid_but_repeated_times_can_recover(void)
{
    MotionMetricsState same_time = {0};
    for (unsigned i = 0; i < 6; i++)
    {
        MotionMetrics result = on_x_axis(&same_time, (float)i, 100);
        assert(!result.magnitude_slope_valid && !result.msd_slope_valid);
    }
    MotionMetrics recovered = on_x_axis(&same_time, 6, 200);
    assert(recovered.magnitude_slope_valid && recovered.msd_slope_valid);
    close_to(recovered.magnitude_slope, 25);
    close_to(recovered.msd_slope, 0);

    MotionMetricsState repeated = {0};
    const uint32_t times[5] = {0, 100, 100, 300, 400};
    const float values[5] = {0, 1, 1, 3, 4};
    MotionMetrics result = {0};
    for (unsigned i = 0; i < 5; i++)
        result = on_x_axis(&repeated, values[i], times[i]);
    assert(result.magnitude_slope_valid);
    close_to(result.magnitude_slope, 10);
}

int main(void)
{
    startup_requires_complete_windows();
    signed_slopes_use_actual_irregular_timestamps();
    rolling_window_evicts_old_values();
    constant_and_symmetric_spikes_have_zero_slope();
    msd_slopes_can_increase_or_decrease();
    magnitude_uses_all_axes_without_signed_cancellation();
    rotation_changes_axis_msd_but_not_constant_magnitude();
    tick_wrap_and_long_uptime_do_not_change_slopes();
    zero_time_variance_is_invalid_but_repeated_times_can_recover();
    puts("PASS: magnitude without axis cancellation, rotation versus axis MSD, five-point warm-up, signed irregular-time regression, rolling eviction, constant/spike cases, tick wrap, zero-time guard, independent sensor states");
}
