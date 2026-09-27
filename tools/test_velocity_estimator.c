#include "velocity_estimator.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

static VelocityEstimate sample(VelocityEstimator *s, int x,int y,int z,float gx,float gy,float gz,unsigned t)
{
    int16_t a[3]={(int16_t)x,(int16_t)y,(int16_t)z};
    float w[3]={gx,gy,gz};
    return VelocityEstimator_Update(s,a,w,t);
}
static void near(float actual,float expected,float tol) { if (!(fabsf(actual-expected)<tol)) { fprintf(stderr,"near: actual=%f expected=%f tol=%f\n",actual,expected,tol); assert(0); } }
static VelocityEstimate settle(VelocityEstimator *s,int x,int y,int z,unsigned start)
{
    VelocityEstimate o={0};
    for(unsigned i=0;i<25;++i) o=sample(s,x,y,z,1000,-700,400,start+100*i);
    assert(o.ready && o.stationary);
    near(sqrtf(o.q[0]*o.q[0]+o.q[1]*o.q[1]+o.q[2]*o.q[2]+o.q[3]*o.q[3]),1,0.001f);
    for(int i=0;i<3;++i) near(o.linear_accel[i],0,0.06f);
    return o;
}
int main(void)
{
    VelocityEstimator s;
    VelocityEstimate o={0};
    VelocityEstimator_Init(&s);
    settle(&s,0,0,1000,0);
    VelocityEstimator_Init(&s); settle(&s,600,0,800,0);
    VelocityEstimator_Init(&s); settle(&s,0,-800,600,0);
    VelocityEstimator_Init(&s); settle(&s,0,0,-1000,0);

    VelocityEstimator_Init(&s); settle(&s,0,0,1000,0);
    /* A positive roll error must decay, establishing Mahony feedback sign. */
    s.output.q[0]=cosf(0.2f); s.output.q[1]=sinf(0.2f);
    s.output.q[2]=s.output.q[3]=0;
    for(unsigned i=0;i<30;++i) sample(&s,0,0,1000,1000,-700,400,2500+100*i);
    assert(fabsf(s.output.q[1])<0.08f);
    /* Bias updates are slow and bounded. Repeated static poses constrain only
       the projection along each observed gravity direction. */
    float before=s.accel_bias[2];
    for(unsigned i=0;i<50;++i) sample(&s,0,0,1020,1000,-700,400,5500+100*i);
    assert(s.accel_bias[2]>before && s.accel_bias[2]<0.1f);
    float gyro_before=s.gyro_bias[0];
    for(unsigned i=0;i<50;++i) sample(&s,0,0,1000,1200,-700,400,10500+100*i);
    assert(s.gyro_bias[0]>gyro_before);
    VelocityEstimator_Invalidate(&s);
    for(unsigned i=0;i<25;++i) o=sample(&s,620,0,800,1000,-700,400,16000+100*i);
    assert(o.ready && o.stationary);
    for(int i=0;i<3;++i) near(o.linear_accel[i],0,0.15f);
    for(unsigned i=0;i<50;++i) sample(&s,620,0,800,1000,-700,400,18500+100*i);
    assert(s.accel_bias[0]>0 && fabsf(s.accel_bias[0])<0.1f);

    VelocityEstimator_Init(&s); settle(&s,0,0,1000,0);
    /* 4 m/s^2 pulse for 1 s, then symmetric braking. Trapezoid starts on
       the second moving sample after ZUPT, so peak should be about 3.6 m/s. */
    for(unsigned i=0;i<10;++i) o=sample(&s,408,0,1000,1000,-700,400,2500+100*i);
    near(o.velocity[0],3.6f,0.25f);
    for(unsigned i=0;i<10;++i) o=sample(&s,-408,0,1000,1000,-700,400,3500+100*i);
    near(o.velocity[0],0.0f,0.3f);
    for(unsigned i=0;i<25;++i) o=sample(&s,0,0,1000,1000,-700,400,4500+100*i);
    assert(o.stationary && o.velocity[0]==0 && o.velocity[1]==0 && o.velocity[2]==0);

    o=sample(&s,0,0,1000,1000,-700,400,6900); /* duplicate */
    assert(!o.ready && o.speed==0);
    settle(&s,0,0,1000,7000);
    o=sample(&s,0,0,1000,1000,-700,400,9700); /* >250 ms */
    assert(!o.ready && o.speed==0);
    float bad[3]={NAN,0,0}; int16_t a[3]={0,0,1000};
    o=VelocityEstimator_Update(&s,a,bad,9800);
    assert(!o.ready && o.speed==0);
    VelocityEstimator_Init(&s);
    settle(&s,0,0,1000,UINT32_MAX-2400u);
    o=sample(&s,0,0,1000,1000,-700,400,100u);
    assert(o.ready && o.stationary);
    printf("velocity estimator host tests passed\n");
    return 0;
}
