#ifndef VELOCITY_ESTIMATOR_H
#define VELOCITY_ESTIMATOR_H

#include <stdbool.h>
#include <stdint.h>

/* Body axes follow the BSP accelerometer and gyroscope axes. World +Z is the
 * stationary specific-force direction; world X/Y are relative to startup yaw.
 * Velocity is short-term: constant-speed motion is indistinguishable from rest
 * with this six-axis IMU, and drift between ZUPTs cannot be removed in software. */
typedef struct {
    float q[4];                 /* body-to-world, w,x,y,z */
    float linear_accel[3];      /* world m/s^2 */
    float velocity[3];          /* world m/s */
    float speed;                /* m/s */
    bool stationary;
    bool ready;
} VelocityEstimate;

typedef struct {
    VelocityEstimate output;
    float gyro_bias[3];         /* rad/s */
    float accel_bias[3];        /* m/s^2 */
    float quiet_accel_sum[3], quiet_gyro_sum[3];
    float previous_linear[3], previous_accel[3];
    float accel_window[10][3]; /* about one second at the 10 Hz loop cadence */
    unsigned accel_window_count, accel_window_next;
    uint32_t previous_time_ms, quiet_start_ms, stationary_start_ms;
    unsigned quiet_count;
    bool has_time, has_previous_linear, has_previous_accel;
} VelocityEstimator;

void VelocityEstimator_Init(VelocityEstimator *state);
void VelocityEstimator_Invalidate(VelocityEstimator *state);
VelocityEstimate VelocityEstimator_Update(VelocityEstimator *state,
                                          const int16_t accel_mg[3],
                                          const float gyro_mdps[3],
                                          uint32_t sample_time_ms);

#endif
