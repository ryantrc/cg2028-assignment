#ifndef MOTION_METRICS_H
#define MOTION_METRICS_H

#include <stdbool.h>
#include <stdint.h>

#define MOTION_SLOPE_WINDOW_SAMPLES 5U

/* Bounded, chronological history for a signed least-squares slope. */
typedef struct
{
    double values[MOTION_SLOPE_WINDOW_SAMPLES];
    uint32_t times_ms[MOTION_SLOPE_WINDOW_SAMPLES];
    unsigned int count;
} MotionSlopeWindow;

/* Keep an independent state for each sensor. XYZ and their signed arithmetic
 * average must use the same physical units. Only the latest five Avg values
 * and latest five valid MSD values contribute to their respective slopes. */
typedef struct
{
    bool has_previous_sample;
    double previous_xyz[3];
    MotionSlopeWindow average_history;
    MotionSlopeWindow msd_history;
} MotionMetricsState;

typedef struct
{
    double msd;
    double average_slope;
    double msd_slope;
    bool msd_valid;
    bool average_slope_valid;
    bool msd_slope_valid;
} MotionMetrics;

static inline bool MotionSlopeWindow_Update(MotionSlopeWindow *window,
                                            double value,
                                            uint32_t time_ms,
                                            double *slope)
{
    *slope = 0.0;
    if (window->count == MOTION_SLOPE_WINDOW_SAMPLES)
    {
        /* Discard the oldest point before storing the newest one. */
        for (unsigned int i = 1; i < window->count; i++)
        {
            window->values[i - 1] = window->values[i];
            window->times_ms[i - 1] = window->times_ms[i];
        }
        window->count--;
    }
    window->values[window->count] = value;
    window->times_ms[window->count] = time_ms;
    window->count++;
    if (window->count < MOTION_SLOPE_WINDOW_SAMPLES)
    {
        return false;
    }

    /* Work in seconds relative to this window's oldest point. Unsigned
     * adjacent tick differences handle rollover without growing timestamps
     * that would lose precision after a long uptime. Consecutive points must
     * be less than one full 32-bit tick cycle apart. */
    double seconds[MOTION_SLOPE_WINDOW_SAMPLES] = {0};
    double time_sum = 0.0;
    double value_sum = 0.0;
    for (unsigned int i = 0; i < MOTION_SLOPE_WINDOW_SAMPLES; i++)
    {
        if (i > 0)
        {
            uint32_t delta_ms = (uint32_t)(window->times_ms[i] - window->times_ms[i - 1]);
            seconds[i] = seconds[i - 1] + delta_ms / 1000.0;
        }
        time_sum += seconds[i];
        value_sum += window->values[i];
    }
    double mean_time = time_sum / MOTION_SLOPE_WINDOW_SAMPLES;
    double mean_value = value_sum / MOTION_SLOPE_WINDOW_SAMPLES;
    double covariance = 0.0;
    double time_variance = 0.0;
    for (unsigned int i = 0; i < MOTION_SLOPE_WINDOW_SAMPLES; i++)
    {
        double centered_time = seconds[i] - mean_time;
        covariance += centered_time * (window->values[i] - mean_value);
        time_variance += centered_time * centered_time;
    }
    if (time_variance == 0.0)
    {
        return false; /* All five timestamps are identical. */
    }
    *slope = covariance / time_variance;
    return true;
}

static inline MotionMetrics MotionMetrics_Update(MotionMetricsState *state,
                                                 const float xyz[3],
                                                 double average,
                                                 uint32_t time_ms)
{
    MotionMetrics result = {0};

    if (state->has_previous_sample)
    {
        for (int axis = 0; axis < 3; axis++)
        {
            double difference = (double)xyz[axis] - state->previous_xyz[axis];
            result.msd += difference * difference;
        }
        result.msd /= 3.0;
        result.msd_valid = true;
        /* MSD first exists at sample 2, so five real MSD values are available
         * at sample 6. Never insert an artificial zero for the first sample. */
        result.msd_slope_valid = MotionSlopeWindow_Update(
            &state->msd_history, result.msd, time_ms, &result.msd_slope);
    }

    /* The fifth sample completes the first window for the signed XYZ Avg. */
    result.average_slope_valid = MotionSlopeWindow_Update(
        &state->average_history, average, time_ms, &result.average_slope);
    for (int axis = 0; axis < 3; axis++)
    {
        state->previous_xyz[axis] = xyz[axis];
    }
    state->has_previous_sample = true;
    return result;
}

#endif /* MOTION_METRICS_H */
