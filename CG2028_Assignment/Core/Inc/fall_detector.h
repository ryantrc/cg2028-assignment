#ifndef FALL_DETECTOR_H
#define FALL_DETECTOR_H

#include <stdbool.h>
#include <stdint.h>

typedef enum
{
    FALL_STATE_WARMUP,
    FALL_STATE_NORMAL,
    FALL_STATE_OBSERVING,
    FALL_STATE_UNCERTAIN,
    FALL_STATE_FALL_LATCHED,
    FALL_STATE_SENSOR_FAULT
} FallDetectorState;

typedef enum
{
    FALL_EVENT_NONE,
    FALL_EVENT_READY,
    FALL_EVENT_SPIKE,
    FALL_EVENT_NEAR_FALL,
    FALL_EVENT_FALL,
    FALL_EVENT_UNCERTAIN,
    FALL_EVENT_SENSOR_FAULT,
    FALL_EVENT_RESTARTED
} FallDetectorEvent;

typedef struct
{
    uint32_t time_ms;
    bool valid;
    bool msd_valid;
    double accel_msd;
    double gyro_msd;
    double gyro_magnitude;
} FallDetectorInput;

typedef struct
{
    FallDetectorState state;
    bool fall_latched;

    bool sensor_fault_active;
    bool has_last_sample;
    bool warmup_started;
    bool trigger_armed;
    uint32_t last_sample_ms;
    uint32_t warmup_start_ms;
    uint32_t candidate_start_ms;
    uint32_t block_start_ms;
    unsigned int completed_blocks;
    unsigned int quiet_blocks;
    unsigned int moving_blocks;

    unsigned int block_samples;
    uint32_t block_first_offset_ms;
    uint32_t block_last_offset_ms;
    double block_accel_msd_mean;
    double block_gyro_msd_mean;
    double block_gyro_magnitude_mean;
} FallDetector;

typedef enum
{
    FALL_BLOCK_UNCLASSIFIED,
    FALL_BLOCK_QUIET,
    FALL_BLOCK_MOVING
} FallBlockClassification;

const char *FallDetector_StateName(FallDetectorState state);
void FallDetector_Init(FallDetector *detector);
FallDetectorEvent FallDetector_Update(FallDetector *detector,
                                      const FallDetectorInput *input);

#endif /* FALL_DETECTOR_H */
