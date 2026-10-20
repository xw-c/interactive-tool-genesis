#include "plasticity.h"
#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
using namespace uipc;
using namespace uipc::backend::cuda;
namespace snk=sym::stable_neo_hookean_3d;

__host__ __device__ double verify_material() {
    const double mu=2000,lambda=10000,yield=200,hardening=500,alpha=.15;
    Matrix3x3 F;F<<1.5,.12,.04,.03,.85,.07,0,.02,1.1;
    auto r=plastic_trial(F,mu,lambda,alpha,yield,hardening);
    if(!r.valid || !r.active)return 1e10;
    double error=fabs(r.yield_residual)/mu;
    Matrix3x3 inverse=Matrix3x3::Identity();double a=alpha;
    plastic_commit(F,inverse,a,mu,lambda,yield,hardening);
    error=fmax(error,fabs(inverse.determinant()-1));
    auto next=plastic_trial(F*inverse,mu,lambda,a,yield,hardening);
    error=fmax(error,next.increment);
    double before;snk::E(before,mu,lambda,F);
    error=fmax(error,fmax(0.0,r.energy-before)/mu);
    Matrix3x3 hydro=1.3*Matrix3x3::Identity();auto h=plastic_trial(hydro,mu,lambda,alpha,yield,hardening);
    error=fmax(error,h.increment);
    Matrix3x3 R=Matrix3x3::Identity();R(0,0)=R(1,1)=cos(.7);R(0,1)=-sin(.7);R(1,0)=sin(.7);
    Matrix3x3 P,Pr;plastic_derivatives(F,mu,lambda,r,P,nullptr);
    auto rotated=plastic_trial(R*F,mu,lambda,alpha,yield,hardening);
    plastic_derivatives(R*F,mu,lambda,rotated,Pr,nullptr);
    error=fmax(error,(Pr-R*P).norm()/mu);
    Matrix3x3 bad=F;bad.col(0)*=-1;
    if(plastic_trial(bad,mu,lambda,alpha,yield,hardening).valid)error=1e10;
    return error;
}
__global__ void gpu_check(double* result) {
    *result=verify_material();
    Matrix3x3 F=Matrix3x3::Identity();F(0,0)=1.5;F(1,1)=.85;F(2,2)=1.1;F(0,1)=.12;
    const auto r=plastic_trial(F,2000,10000,.15,200,500);
    Matrix3x3 P;Matrix9x9 K;plastic_derivatives(F,2000,10000,r,P,&K);
    if(!isfinite(K.norm()) || !isfinite(P.norm()))*result=1e10;
    for(int i=0;i<9;++i)result[1+i]=P.data()[i];
    for(int i=0;i<81;++i)result[10+i]=K.data()[i];
}

bool finite_difference(Matrix3x3 F,double mu,double lambda,double alpha,double y,double H) {
    auto r=plastic_trial(F,mu,lambda,alpha,y,H);
    if(!r.valid){std::printf("local solve failed\n");return false;}
    Matrix3x3 P;Matrix9x9 K;plastic_derivatives(F,mu,lambda,r,P,&K);
    double eg=0,eh=0,eps=2e-6;
    for(int col=0;col<9;++col) {
        Matrix3x3 plus=F,minus=F;plus.data()[col]+=eps;minus.data()[col]-=eps;
        auto rp=plastic_trial(plus,mu,lambda,alpha,y,H),rm=plastic_trial(minus,mu,lambda,alpha,y,H);
        if(!rp.valid || !rm.valid)return false;
        Matrix3x3 pp,pm;plastic_derivatives(plus,mu,lambda,rp,pp,nullptr);plastic_derivatives(minus,mu,lambda,rm,pm,nullptr);
        eg=fmax(eg,fabs((rp.energy-rm.energy)/(2*eps)-P.data()[col])/fmax(mu,P.norm()));
        Vector9 numerical=Eigen::Map<Vector9>(pp.data())-Eigen::Map<Vector9>(pm.data());numerical/=2*eps;
        eh=fmax(eh,(numerical-K.col(col)).norm()/fmax(mu,K.col(col).norm()));
    }
    const double symmetry=(K-K.transpose()).norm()/fmax(1.0,K.norm());
    std::printf("FD active=%d gradient=%.4g tangent=%.4g symmetry=%.4g yield=%.4g\n",r.active,eg,eh,symmetry,r.yield_residual);
    return eg<2e-6 && eh<2e-5 && symmetry<1e-10;
}
int main() {
    bool pass=true;const double host=verify_material();std::printf("host_material_error %.12g\n",host);pass &= host<1e-8;
    if(!std::getenv("IPC_CPU_ONLY")) {
        double* device;cudaMallocManaged(&device,91*sizeof(double));gpu_check<<<1,1>>>(device);
    auto err=cudaDeviceSynchronize();if(err!=cudaSuccess){std::printf("CUDA %s\n",cudaGetErrorString(err));pass=false;}
    if(err==cudaSuccess) {
        std::printf("GPU_material_error %.12g\n",*device);pass &= *device<1e-8;
        Matrix3x3 F=Matrix3x3::Identity();F(0,0)=1.5;F(1,1)=.85;F(2,2)=1.1;F(0,1)=.12;
        const auto r=plastic_trial(F,2000,10000,.15,200,500);
        Matrix3x3 P;Matrix9x9 K;plastic_derivatives(F,2000,10000,r,P,&K);
        double error=0;
        for(int i=0;i<9;++i)error=fmax(error,fabs(P.data()[i]-device[1+i])/2000);
        for(int i=0;i<81;++i)error=fmax(error,fabs(K.data()[i]-device[10+i])/2000);
        std::printf("GPU_CPU_stress_tangent_error %.12g\n",error);pass &= error<1e-9;
    }
    cudaFree(device);
    }
    Matrix3x3 F;F<<1.5,.12,.04,.03,.85,.07,0,.02,1.1;
    for(double H: {0.,500.,10000.})pass &= finite_difference(F,2000,10000,.15,200,H);
    pass &= finite_difference(F,2000,10000,0,1e8,0);
    F.setZero();F.diagonal()<<1.4,1.4,.8;
    pass &= finite_difference(F,2000,10000,0,200,500);
    F(1,1)+=1e-9;pass &= finite_difference(F,2000,10000,0,200,500);
    F.setIdentity();F(0,1)=.7;pass &= finite_difference(F,2000,10000,0,200,500);
    // Stress-based yield and hardening consistency through cyclic non-coaxial history.
    Matrix3x3 inverse=Matrix3x3::Identity();double alpha=0;
    for(int i=0;i<120;++i) {
        double t=i*.07;F.setIdentity();F(0,0)=exp(.35*sin(t));F(1,1)=exp(-.2*sin(t));F(2,2)=1/(F(0,0)*F(1,1));F(0,1)=.3*cos(1.7*t);
        auto r=plastic_trial(F*inverse,2000,10000,alpha,200,500);
        if(!r.valid){pass=false;break;}
        double old=alpha;plastic_commit(F*inverse,inverse,alpha,2000,10000,200,500);
        auto settled=plastic_trial(F*inverse,2000,10000,alpha,200,500);
        pass &= alpha>=old && fabs(inverse.determinant()-1)<1e-10 && settled.valid && settled.increment<1e-7;
    }
    Matrix3x3 histories[6];double accumulated[6];const int counts[6]={20,40,80,160,320,640};
    for(int level=0;level<6;++level) {
        Matrix3x3 A=Matrix3x3::Identity();double a=0;
        for(int step=1;step<=counts[level];++step) {
            const double t=double(step)/counts[level],stretch=.35*sin(3.141592653589793*t);
            Matrix3x3 total=Matrix3x3::Identity();total(0,0)=exp(stretch);
            total(1,1)=total(2,2)=exp(-stretch/2);total(0,1)=.3*sin(6.283185307179586*t);
            Matrix3x3 trial=total*A;const auto r=plastic_trial(trial,2000,10000,a,200,500);
            double before;snk::E(before,2000.,10000.,trial);
            if(!r.valid || r.energy>before+1e-6){pass=false;break;}
            plastic_commit(trial,A,a,2000,10000,200,500);
        }
        histories[level]=A;accumulated[level]=a;
    }
    double errors[6];
    for(int level=0;level<6;++level) {
        errors[level]=(histories[level]-histories[5]).norm()+fabs(accumulated[level]-accumulated[5]);
        std::printf("HISTORY steps=%d alpha=%.12g reference_error=%.12g\n",counts[level],accumulated[level],errors[level]);
    }
    pass &= errors[4]<errors[1] && errors[1]<errors[0];
    std::printf(pass?"PASS\n":"FAIL\n");return pass?0:1;
}
