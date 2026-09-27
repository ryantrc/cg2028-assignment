#include "velocity_estimator.h"
#include <math.h>
#include <string.h>

#define G_MPS2 9.80665f
#define MG_TO_MPS2 (G_MPS2 / 1000.0f)
#define MDPS_TO_RADPS (0.017453292519943295f / 1000.0f)
#define MAX_GAP_MS 250u
#define QUIET_MS 1000u
#define STATIONARY_MS 1000u
/* Initial detector thresholds; tune using recorded stationary board data.
 * The window limits sample-to-sample and longer variation over ten readings. */
#define ACCEL_GRAVITY_TOL_MPS2 0.65f
#define GYRO_QUIET_RADPS 0.12f
#define ACCEL_VARIATION_MPS2 0.35f
#define MAHONY_KP_PER_S 1.5f
#define GYRO_BIAS_RATE_PER_S 0.05f
#define ACCEL_BIAS_RATE_PER_S 0.015f
#define ACCEL_BIAS_MAX_MPS2 0.5f

static float norm3(const float v[3])
{
    return sqrtf(v[0]*v[0] + v[1]*v[1] + v[2]*v[2]);
}

static void rotate(const float q[4], const float v[3], float out[3])
{
    float t[3] = {2.0f*(q[2]*v[2]-q[3]*v[1]),
                  2.0f*(q[3]*v[0]-q[1]*v[2]),
                  2.0f*(q[1]*v[1]-q[2]*v[0])};
    out[0] = v[0] + q[0]*t[0] + q[2]*t[2] - q[3]*t[1];
    out[1] = v[1] + q[0]*t[1] + q[3]*t[0] - q[1]*t[2];
    out[2] = v[2] + q[0]*t[2] + q[1]*t[1] - q[2]*t[0];
}

static void clear_tracking(VelocityEstimator *s)
{
    memset(&s->output, 0, sizeof(s->output));
    s->output.q[0] = 1.0f;
    memset(s->quiet_accel_sum, 0, sizeof(s->quiet_accel_sum));
    memset(s->quiet_gyro_sum, 0, sizeof(s->quiet_gyro_sum));
    memset(s->previous_linear, 0, sizeof(s->previous_linear));
    s->quiet_count = 0;
    s->has_previous_linear = false;
    s->has_previous_accel = false;
    s->accel_window_count = s->accel_window_next = 0;
    s->stationary_start_ms = 0;
}

void VelocityEstimator_Init(VelocityEstimator *s)
{
    memset(s, 0, sizeof(*s));
    s->output.q[0] = 1.0f;
}

void VelocityEstimator_Invalidate(VelocityEstimator *s)
{
    clear_tracking(s); /* credible bias survives a missing sample */
    s->has_time = false;
}

static void align_gravity(float q[4], const float a[3])
{
    float n = norm3(a);
    float z = a[2] / n;
    if (z < -0.9999f) {
        q[0]=0.0f; q[1]=1.0f; q[2]=0.0f; q[3]=0.0f;
    } else {
        float scale = sqrtf(2.0f*(1.0f+z));
        q[0]=0.5f*scale; q[1]=a[1]/(n*scale);
        q[2]=-a[0]/(n*scale); q[3]=0.0f;
    }
}

VelocityEstimate VelocityEstimator_Update(VelocityEstimator *s,
    const int16_t accel_mg[3], const float gyro_mdps[3], uint32_t time_ms)
{
    float a[3], w[3];
    uint32_t elapsed = s->has_time ? (uint32_t)(time_ms-s->previous_time_ms) : 0u;
    if (!accel_mg || !gyro_mdps) { VelocityEstimator_Invalidate(s); return s->output; }
    for (int i=0; i<3; ++i) {
        if (!isfinite(gyro_mdps[i])) { VelocityEstimator_Invalidate(s); return s->output; }
        a[i] = accel_mg[i]*MG_TO_MPS2;
        w[i] = gyro_mdps[i]*MDPS_TO_RADPS;
    }
    if (s->has_time && (elapsed == 0u || elapsed > MAX_GAP_MS)) {
        VelocityEstimator_Invalidate(s);
    }
    bool first = !s->has_time;
    s->previous_time_ms = time_ms;
    s->has_time = true;
    if (first || elapsed == 0u || elapsed > MAX_GAP_MS) return s->output;
    float dt = elapsed*0.001f;
    float corrected[3], rate[3];
    for (int i=0; i<3; ++i) {
        corrected[i]=a[i]-s->accel_bias[i];
        rate[i]=w[i]-s->gyro_bias[i];
    }
    float magnitude=norm3(corrected);
    float variation=0.0f;
    for (unsigned k=0;k<s->accel_window_count;++k) {
        float diff[3];
        for (int i=0;i<3;++i) diff[i]=corrected[i]-s->accel_window[k][i];
        float distance=norm3(diff);
        if (distance>variation) variation=distance;
    }
    for (int i=0;i<3;++i) s->accel_window[s->accel_window_next][i]=corrected[i];
    s->accel_window_next=(s->accel_window_next+1u)%10u;
    if (s->accel_window_count<10u) ++s->accel_window_count;
    for (int i=0;i<3;++i) s->previous_accel[i]=corrected[i];
    s->has_previous_accel=true;
    bool quiet = isfinite(magnitude) &&
        fabsf(magnitude-G_MPS2)<ACCEL_GRAVITY_TOL_MPS2 &&
        norm3(rate)<GYRO_QUIET_RADPS && s->accel_window_count>=2u &&
        variation<ACCEL_VARIATION_MPS2;
    if (!s->output.ready) {
        if (!quiet) { s->quiet_count=0; memset(s->quiet_accel_sum,0,sizeof(s->quiet_accel_sum));
                      memset(s->quiet_gyro_sum,0,sizeof(s->quiet_gyro_sum)); return s->output; }
        if (!s->quiet_count) s->quiet_start_ms=time_ms;
        ++s->quiet_count;
        for (int i=0;i<3;++i) { s->quiet_accel_sum[i]+=a[i]; s->quiet_gyro_sum[i]+=w[i]; }
        if ((uint32_t)(time_ms-s->quiet_start_ms)<QUIET_MS || s->quiet_count<2u) return s->output;
        float avg[3];
        for (int i=0;i<3;++i) { avg[i]=s->quiet_accel_sum[i]/s->quiet_count-s->accel_bias[i];
                                  s->gyro_bias[i]=s->quiet_gyro_sum[i]/s->quiet_count; }
        if (norm3(avg)<1.0f) { VelocityEstimator_Invalidate(s); return s->output; }
        align_gravity(s->output.q,avg);
        s->output.ready=true; s->output.stationary=true;
        s->stationary_start_ms=time_ms;
        return s->output;
    }

    float *q=s->output.q;
    if (fabsf(magnitude-G_MPS2)<ACCEL_GRAVITY_TOL_MPS2 && magnitude>1.0f) {
        /* Mahony proportional term: measured gravity cross predicted body +Z.
         * This sign reduces positive roll/pitch tilt error for body-to-world q. */
        float predicted[3] = {2.0f*(q[1]*q[3]-q[0]*q[2]),
                              2.0f*(q[2]*q[3]+q[0]*q[1]),
                              1.0f-2.0f*(q[1]*q[1]+q[2]*q[2])};
        float u[3]={corrected[0]/magnitude,corrected[1]/magnitude,corrected[2]/magnitude};
        float error[3]={u[1]*predicted[2]-u[2]*predicted[1],
                        u[2]*predicted[0]-u[0]*predicted[2],
                        u[0]*predicted[1]-u[1]*predicted[0]};
        for (int i=0;i<3;++i) rate[i]+=MAHONY_KP_PER_S*error[i];
    }
    float old[4]={q[0],q[1],q[2],q[3]};
    q[0]+=0.5f*dt*(-old[1]*rate[0]-old[2]*rate[1]-old[3]*rate[2]);
    q[1]+=0.5f*dt*(old[0]*rate[0]+old[2]*rate[2]-old[3]*rate[1]);
    q[2]+=0.5f*dt*(old[0]*rate[1]+old[3]*rate[0]-old[1]*rate[2]);
    q[3]+=0.5f*dt*(old[0]*rate[2]+old[1]*rate[1]-old[2]*rate[0]);
    float qnorm=sqrtf(q[0]*q[0]+q[1]*q[1]+q[2]*q[2]+q[3]*q[3]);
    if (!isfinite(qnorm) || qnorm<0.5f) { VelocityEstimator_Invalidate(s); return s->output; }
    for (int i=0;i<4;++i) q[i]/=qnorm;
    rotate(q,corrected,s->output.linear_accel);
    s->output.linear_accel[2]-=G_MPS2;
    if (quiet) {
        if (!s->stationary_start_ms) s->stationary_start_ms=time_ms;
    } else s->stationary_start_ms=0;
    s->output.stationary=quiet && s->stationary_start_ms &&
        (uint32_t)(time_ms-s->stationary_start_ms)>=STATIONARY_MS;
    if (s->output.stationary) {
        memset(s->output.velocity,0,sizeof(s->output.velocity));
        s->has_previous_linear=false;
        float gyro_step=GYRO_BIAS_RATE_PER_S*dt;
        float accel_step=ACCEL_BIAS_RATE_PER_S*dt;
        float residual=magnitude-G_MPS2;
        for (int i=0;i<3;++i) {
            s->gyro_bias[i]+=gyro_step*(w[i]-s->gyro_bias[i]);
            float candidate=s->accel_bias[i]+accel_step*residual*corrected[i]/magnitude;
            s->accel_bias[i]=fmaxf(-ACCEL_BIAS_MAX_MPS2,fminf(ACCEL_BIAS_MAX_MPS2,candidate));
        }
    } else {
        if (s->has_previous_linear)
            for (int i=0;i<3;++i) s->output.velocity[i]+=0.5f*dt*(s->previous_linear[i]+s->output.linear_accel[i]);
        for (int i=0;i<3;++i) s->previous_linear[i]=s->output.linear_accel[i];
        s->has_previous_linear=true;
    }
    s->output.speed=norm3(s->output.velocity);
    if (!isfinite(s->output.speed)) { VelocityEstimator_Invalidate(s); }
    return s->output;
}
