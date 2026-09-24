#ifndef FALL_DETECTOR_H
#define FALL_DETECTOR_H

#include <math.h>
#include <stdbool.h>
#include <stdint.h>

/* These thresholds apply to the existing 100 ms, EWMA-filtered samples.
 * Acceleration MSD is in (m/s^2)^2; gyro MSD is in (degrees/s)^2;
 * gyro magnitude is in degrees/s. They are prototype thresholds, not a
 * medically validated fall-detection rule. */
#define FALL_TRIGGER_ACCEL_MSD 5.0
#define FALL_QUIET_ACCEL_MSD 0.005
#define FALL_QUIET_GYRO_MSD 1.0
#define FALL_QUIET_GYRO_MAGNITUDE 5.0
#define FALL_WARMUP_MS 2000U
#define FALL_MAX_SAMPLE_GAP_MS 250U
#define FALL_IGNORE_AFTER_TRIGGER_MS 5000U
#define FALL_BLOCK_MS 1000U
#define FALL_REQUIRED_BLOCKS 3U
#define FALL_OBSERVATION_MS \
    (FALL_IGNORE_AFTER_TRIGGER_MS + FALL_REQUIRED_BLOCKS * FALL_BLOCK_MS)

/* A block needs observations across almost all of its one-second interval.
 * Ordinary 100/101 ms sampling supplies nine or ten samples. A clock reaching
 * the end of a block, by itself, is not evidence that motion was observed. */
#define FALL_BLOCK_MIN_SAMPLES 9U
#define FALL_BLOCK_MAX_FIRST_OFFSET_MS 150U
#define FALL_BLOCK_MIN_LAST_OFFSET_MS 850U
#define FALL_BLOCK_MIN_SPAN_MS 750U

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
    bool valid;     /* Caller checked sensor initialization and read health. */
    bool msd_valid; /* The first sample after a history reset has no MSD. */
    double accel_msd;
    double gyro_msd;
    double gyro_magnitude;
} FallDetectorInput;

typedef struct
{
    /* These two fields are the public current state and alarm indication. */
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

static inline const char *FallDetector_StateName(FallDetectorState state)
{
    switch (state)
    {
    case FALL_STATE_WARMUP: return "WARMUP";
    case FALL_STATE_NORMAL: return "NORMAL";
    case FALL_STATE_OBSERVING: return "OBSERVING";
    case FALL_STATE_UNCERTAIN: return "UNCERTAIN";
    case FALL_STATE_FALL_LATCHED: return "FALL_LATCHED";
    case FALL_STATE_SENSOR_FAULT: return "SENSOR_FAULT";
    default: return "UNKNOWN";
    }
}

static inline void FallDetector_ClearBlock(FallDetector *detector)
{
    detector->block_samples = 0U;
    detector->block_first_offset_ms = 0U;
    detector->block_last_offset_ms = 0U;
    detector->block_accel_msd_mean = 0.0;
    detector->block_gyro_msd_mean = 0.0;
    detector->block_gyro_magnitude_mean = 0.0;
}

static inline void FallDetector_ClearCandidate(FallDetector *detector)
{
    detector->candidate_start_ms = 0U;
    detector->block_start_ms = 0U;
    detector->completed_blocks = 0U;
    detector->quiet_blocks = 0U;
    detector->moving_blocks = 0U;
    FallDetector_ClearBlock(detector);
}

static inline void FallDetector_Init(FallDetector *detector)
{
    *detector = (FallDetector){0};
    detector->state = FALL_STATE_WARMUP;
    detector->trigger_armed = true;
}

static inline void FallDetector_StartWarmup(FallDetector *detector,
                                           uint32_t time_ms)
{
    FallDetector_ClearCandidate(detector);
    detector->state = FALL_STATE_WARMUP;
    detector->warmup_started = true;
    detector->warmup_start_ms = time_ms;
    detector->trigger_armed = true;
}

static inline FallBlockClassification FallDetector_ClassifyBlock(
    const FallDetector *detector)
{
    if (detector->block_samples < FALL_BLOCK_MIN_SAMPLES ||
        detector->block_first_offset_ms > FALL_BLOCK_MAX_FIRST_OFFSET_MS ||
        detector->block_last_offset_ms < FALL_BLOCK_MIN_LAST_OFFSET_MS ||
        detector->block_last_offset_ms - detector->block_first_offset_ms <
            FALL_BLOCK_MIN_SPAN_MS)
    {
        return FALL_BLOCK_UNCLASSIFIED;
    }

    double accel_mean = detector->block_accel_msd_mean;
    double gyro_msd_mean = detector->block_gyro_msd_mean;
    double gyro_magnitude_mean = detector->block_gyro_magnitude_mean;
    if (accel_mean < FALL_QUIET_ACCEL_MSD &&
        gyro_msd_mean < FALL_QUIET_GYRO_MSD &&
        gyro_magnitude_mean < FALL_QUIET_GYRO_MAGNITUDE)
    {
        return FALL_BLOCK_QUIET;
    }
    if (accel_mean >= FALL_QUIET_ACCEL_MSD &&
        gyro_msd_mean >= FALL_QUIET_GYRO_MSD &&
        gyro_magnitude_mean >= FALL_QUIET_GYRO_MAGNITUDE)
    {
        return FALL_BLOCK_MOVING;
    }
    return FALL_BLOCK_UNCLASSIFIED; /* The three measurements disagree. */
}

static inline FallDetectorEvent FallDetector_Update(
    FallDetector *detector, const FallDetectorInput *input)
{
    /* An unavailable first MSD is expected. Bad available measurements or a
     * failed read are faults; neither may count as evidence of quiet motion. */
    bool measurements_valid = input->valid &&
        isfinite(input->gyro_magnitude) && input->gyro_magnitude >= 0.0 &&
        (!input->msd_valid ||
         (isfinite(input->accel_msd) && input->accel_msd >= 0.0 &&
          isfinite(input->gyro_msd) && input->gyro_msd >= 0.0));
    if (!measurements_valid)
    {
        bool new_fault = !detector->sensor_fault_active;
        detector->sensor_fault_active = true;
        detector->has_last_sample = false;
        detector->warmup_started = false;
        FallDetector_ClearCandidate(detector);
        if (!detector->fall_latched)
        {
            detector->state = FALL_STATE_SENSOR_FAULT;
        }
        /* A confirmed alarm stays latched even when its sensor then fails. */
        return new_fault ? FALL_EVENT_SENSOR_FAULT : FALL_EVENT_NONE;
    }

    bool recovering_from_fault = detector->sensor_fault_active;
    detector->sensor_fault_active = false;
    if (detector->fall_latched)
    {
        return FALL_EVENT_NONE; /* Only explicit initialization clears this. */
    }

    bool sample_gap = detector->has_last_sample &&
        (uint32_t)(input->time_ms - detector->last_sample_ms) > FALL_MAX_SAMPLE_GAP_MS;
    detector->last_sample_ms = input->time_ms;
    detector->has_last_sample = true;
    if (recovering_from_fault || sample_gap)
    {
        /* The caller also resets its EWMA and motion-metric histories. */
        FallDetector_StartWarmup(detector, input->time_ms);
        return FALL_EVENT_RESTARTED;
    }

    if (!input->msd_valid)
    {
        bool already_warming_up = detector->state == FALL_STATE_WARMUP;
        FallDetector_StartWarmup(detector, input->time_ms);
        return already_warming_up ? FALL_EVENT_NONE : FALL_EVENT_RESTARTED;
    }

    if (detector->state == FALL_STATE_WARMUP)
    {
        if (!detector->warmup_started)
        {
            FallDetector_StartWarmup(detector, input->time_ms);
        }
        if ((uint32_t)(input->time_ms - detector->warmup_start_ms) >= FALL_WARMUP_MS)
        {
            detector->state = FALL_STATE_NORMAL;
            return FALL_EVENT_READY;
        }
        return FALL_EVENT_NONE;
    }

    if (detector->state == FALL_STATE_NORMAL)
    {
        if (!detector->trigger_armed)
        {
            /* One prolonged high reading must not trigger repeated trials. */
            if (input->accel_msd < FALL_TRIGGER_ACCEL_MSD)
            {
                detector->trigger_armed = true;
            }
            return FALL_EVENT_NONE;
        }
        if (input->accel_msd >= FALL_TRIGGER_ACCEL_MSD)
        {
            FallDetector_ClearCandidate(detector);
            detector->candidate_start_ms = input->time_ms;
            detector->block_start_ms = input->time_ms + FALL_IGNORE_AFTER_TRIGGER_MS;
            detector->state = FALL_STATE_OBSERVING;
            return FALL_EVENT_SPIKE;
        }
        return FALL_EVENT_NONE;
    }

    /* Later spikes leave the original candidate time unchanged. The first
     * five seconds allow the abrupt movement and EWMA transient to settle. */
    if (detector->state == FALL_STATE_OBSERVING &&
        (uint32_t)(input->time_ms - detector->candidate_start_ms) <
            FALL_IGNORE_AFTER_TRIGGER_MS)
    {
        return FALL_EVENT_NONE;
    }

    FallDetectorEvent event = FALL_EVENT_NONE;
    uint32_t offset_ms = (uint32_t)(input->time_ms - detector->block_start_ms);
    while (offset_ms >= FALL_BLOCK_MS)
    {
        /* Finish [start,start+1000) before adding the boundary sample to the
         * next block. A mixed or incomplete block breaks both streaks. */
        FallBlockClassification classification = FallDetector_ClassifyBlock(detector);
        detector->quiet_blocks = classification == FALL_BLOCK_QUIET ?
            detector->quiet_blocks + 1U : 0U;
        detector->moving_blocks = classification == FALL_BLOCK_MOVING ?
            detector->moving_blocks + 1U : 0U;
        if (detector->completed_blocks < FALL_REQUIRED_BLOCKS)
        {
            detector->completed_blocks++;
        }

        if (detector->quiet_blocks >= FALL_REQUIRED_BLOCKS)
        {
            detector->fall_latched = true;
            detector->state = FALL_STATE_FALL_LATCHED;
            return FALL_EVENT_FALL;
        }
        if (detector->moving_blocks >= FALL_REQUIRED_BLOCKS)
        {
            detector->state = FALL_STATE_NORMAL;
            detector->trigger_armed = false;
            return FALL_EVENT_NEAR_FALL;
        }
        if (detector->completed_blocks >= FALL_REQUIRED_BLOCKS &&
            detector->state != FALL_STATE_UNCERTAIN)
        {
            detector->state = FALL_STATE_UNCERTAIN;
            event = FALL_EVENT_UNCERTAIN;
        }

        detector->block_start_ms += FALL_BLOCK_MS;
        FallDetector_ClearBlock(detector);
        offset_ms = (uint32_t)(input->time_ms - detector->block_start_ms);
    }

    if (detector->block_samples == 0U)
    {
        detector->block_first_offset_ms = offset_ms;
    }
    detector->block_last_offset_ms = offset_ms;
    detector->block_samples++;
    /* Incremental arithmetic means keep a constant threshold value exact;
     * summing ten copies of 0.005 then dividing can round just below 0.005. */
    detector->block_accel_msd_mean +=
        (input->accel_msd - detector->block_accel_msd_mean) / detector->block_samples;
    detector->block_gyro_msd_mean +=
        (input->gyro_msd - detector->block_gyro_msd_mean) / detector->block_samples;
    detector->block_gyro_magnitude_mean +=
        (input->gyro_magnitude - detector->block_gyro_magnitude_mean) / detector->block_samples;
    return event;
}

#endif /* FALL_DETECTOR_H */
