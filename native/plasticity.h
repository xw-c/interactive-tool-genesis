#pragma once
// Associative, isochoric J2 update for the existing SNK1 elastic energy.
// Incremental variational formulation: Ortiz & Stainier (1999), specialized
// here to isotropic SNK1 + exponential plastic flow + linear hardening.
// See native/README.md for the complete specialization and its limitations.
#include "snk1.h"
#include <cmath>

namespace uipc::backend::cuda {
struct PlasticResult {
    Matrix3x3 V=Matrix3x3::Identity();
    Vector3 eigenvalues=Vector3::Ones(), plastic_log=Vector3::Zero();
    Vector2 z=Vector2::Zero();
    Matrix2x2 local_hessian=Matrix2x2::Identity();
    Float increment=0, energy=0, yield_residual=0;
    bool active=false, valid=true;
};
using Matrix3x2=Eigen::Matrix<Float,3,2>;
__host__ __device__ __forceinline__ Matrix3x2 plastic_basis() {
    Matrix3x2 B;
    B << 0.7071067811865475244,0.4082482904638630164,
        -0.7071067811865475244,0.4082482904638630164,
         0.0,-0.8164965809277260327;
    return B;
}
__host__ __device__ __forceinline__ Matrix2x2 plastic_inverse2(const Matrix2x2& H) {
    Matrix2x2 A;const Float det=H(0,0)*H(1,1)-H(0,1)*H(1,0);
    A(0,0)=H(1,1)/det;A(0,1)=-H(0,1)/det;
    A(1,0)=-H(1,0)/det;A(1,1)=H(0,0)/det;return A;
}
// Double-precision Jacobi eigensystem. Avoid the upstream SVD helper's float
// conversion: local residuals and the consistent tangent need double accuracy.
__host__ __device__ __forceinline__ void plastic_eigen(const Matrix3x3& C,Vector3& values,Matrix3x3& V) {
    Matrix3x3 A=C;V.setIdentity();
    for(int sweep=0;sweep<16;++sweep) {
        for(int p=0;p<2;++p) for(int q=p+1;q<3;++q) {
            const Float off=A(p,q);
            if(fabs(off)<=2e-16*(fabs(A(p,p))+fabs(A(q,q))))continue;
            const Float tau=(A(q,q)-A(p,p))/(2*off);
            const Float t=copysign(1.0,tau)/(fabs(tau)+sqrt(1+tau*tau));
            const Float c=1/sqrt(1+t*t),s=t*c;
            Matrix3x3 R=Matrix3x3::Identity();R(p,p)=R(q,q)=c;R(p,q)=s;R(q,p)=-s;
            A=(R.transpose()*A*R).eval();V=(V*R).eval();
        }
    }
    values=A.diagonal();
}
__host__ __device__ __forceinline__ Float plastic_local(const Vector3& c,const Vector2& z,
    Float mu,Float current_yield,Float hardening,Vector2* gradient,Matrix2x2* hessian) {
    const Matrix3x2 B=plastic_basis();const Float k=sqrt(2.0/3.0),r=z.norm();
    Vector3 x=(c.array()*(-2*(B*z).array()).exp()).matrix();
    const Float value=0.5*mu*x.sum()+current_yield*k*r+0.5*hardening*k*k*r*r;
    if(gradient) {
        const Vector2 n=z/r;
        *gradient=-mu*B.transpose()*x+(current_yield*k+hardening*k*k*r)*n;
        if(hessian)*hessian=2*mu*B.transpose()*x.asDiagonal()*B
            +(current_yield*k/r)*(Matrix2x2::Identity()-n*n.transpose())
            +hardening*k*k*Matrix2x2::Identity();
    }
    return value;
}
__host__ __device__ __forceinline__ PlasticResult plastic_trial(const Matrix3x3& F,
    Float mu,Float lambda,Float alpha,Float yield,Float hardening) {
    PlasticResult result;
    const Float J=F.determinant();
    if(!(J>1e-12) || !isfinite(J)){result.valid=false;result.energy=1e30;return result;}
    plastic_eigen(F.transpose()*F,result.eigenvalues,result.V);
    for(int i=0;i<3;++i)if(!(result.eigenvalues[i]>1e-24)||!isfinite(result.eigenvalues[i])){result.valid=false;result.energy=1e30;return result;}
    const Float k=sqrt(2.0/3.0),current_yield=yield+hardening*alpha;
    const Matrix3x2 B=plastic_basis();
    const Vector2 driving=mu*B.transpose()*result.eigenvalues;
    const Float equivalent=driving.norm()/k;
    const Float scale=fmax(mu,fmax(current_yield,1.0));
    if(equivalent<=current_yield+1e-12*scale) {
        sym::stable_neo_hookean_3d::E(result.energy,mu,lambda,F);
        result.yield_residual=equivalent-current_yield;return result;
    }
    // Strictly convex 2D local minimization. History is not changed here.
    result.active=true;
    const Float estimate=(equivalent-current_yield)/(3*mu+hardening);
    const Vector3 log_stretch=0.5*result.eigenvalues.array().log().matrix();
    const Float relaxed=(B.transpose()*log_stretch).norm();
    Vector2 z=fmax(fmin(estimate/k,relaxed),1e-12)*driving.normalized();
    bool converged=false;
    for(int iteration=0;iteration<64;++iteration) {
        Vector2 g;Matrix2x2 H;
        const Float value=plastic_local(result.eigenvalues,z,mu,current_yield,hardening,&g,&H);
        if(g.norm()<=2e-11*scale){converged=true;break;}
        if(!(H.determinant()>0)||!isfinite(H.determinant()))break;
        const Vector2 step=-plastic_inverse2(H)*g;
        Float length=1;bool accepted=false;
        for(int line=0;line<40;++line) {
            Vector2 next=z+length*step;
            const Float candidate=plastic_local(result.eigenvalues,next,mu,current_yield,hardening,nullptr,nullptr);
            if(next.norm()>1e-18 && isfinite(candidate)
                && candidate<=value+1e-4*length*g.dot(step)+2e-15*scale){z=next;accepted=true;break;}
            length*=0.5;
        }
        if(!accepted)break;
    }
    Vector2 gradient;
    plastic_local(result.eigenvalues,z,mu,current_yield,hardening,&gradient,&result.local_hessian);
    if(!isfinite(gradient.norm()) || !(result.local_hessian.determinant()>0) || (!converged && gradient.norm()>2e-9*scale)){result.valid=false;result.energy=1e30;return result;}
    result.z=z;result.plastic_log=B*z;result.increment=k*z.norm();
    const Vector3 returned=(result.eigenvalues.array()*(-2*result.plastic_log.array()).exp()).matrix();
    const Vector3 stress=mu*(returned.array()-returned.mean()).matrix();
    result.yield_residual=stress.norm()/k-(current_yield+hardening*result.increment);
    const Float j=J-1;
    result.energy=0.5*mu*(returned.sum()-3)+0.5*lambda*j*j-mu*j
        +current_yield*result.increment+0.5*hardening*result.increment*result.increment;
    if(!isfinite(result.energy)) {result.valid=false;result.energy=1e30;}
    return result;
}
// Derivatives of the minimized incremental potential, NOT the old elastic
// Hessian at a returned state. Local variables are eliminated by Schur complement.
__host__ __device__ __forceinline__ void plastic_derivatives(const Matrix3x3& F,
    Float mu,Float lambda,const PlasticResult& r,Matrix3x3& P,Matrix9x9* hessian) {
    if(!r.active) {
        sym::stable_neo_hookean_3d::dEdVecF(P,mu,lambda,F);
        if(hessian)sym::stable_neo_hookean_3d::ddEddVecF(*hessian,mu,lambda,F);
        return;
    }
    const Vector3 c=r.eigenvalues,ratio=(-2*r.plastic_log.array()).exp().matrix();
    const Float J=F.determinant(),beta=lambda*(J-1)-mu;
    Vector3 g=0.5*mu*ratio;
    for(int i=0;i<3;++i)g[i]+=beta*J/(2*c[i]);
    const Matrix3x3 G=r.V*g.asDiagonal()*r.V.transpose();
    P=2*F*G;
    if(!hessian)return;
    const Matrix3x2 cross=-mu*ratio.asDiagonal()*plastic_basis();
    Matrix3x3 K=-cross*plastic_inverse2(r.local_hessian)*cross.transpose();
    for(int i=0;i<3;++i)for(int j=0;j<3;++j) {
        K(i,j)+=(lambda*J*J+beta*J)/(4*c[i]*c[j]);
        if(i==j)K(i,j)-=beta*J/(2*c[i]*c[i]);
    }
    for(int col=0;col<9;++col) {
        Matrix3x3 dF=Matrix3x3::Zero();dF.data()[col]=1;
        const Matrix3x3 dC=r.V.transpose()*(F.transpose()*dF+dF.transpose()*F)*r.V;
        Matrix3x3 dG=Matrix3x3::Zero();dG.diagonal()=K*dC.diagonal();
        for(int i=0;i<3;++i)for(int j=i+1;j<3;++j) {
            Float divided;
            if(fabs(c[i]-c[j])>1e-7*fmax(c[i],c[j]))divided=(g[i]-g[j])/(c[i]-c[j]);
            else divided=0.5*(K(i,i)+K(j,j)-K(i,j)-K(j,i));
            dG(i,j)=dG(j,i)=divided*dC(i,j);
        }
        const Matrix3x3 dP=2*dF*G+2*F*r.V*dG*r.V.transpose();
        for(int row=0;row<9;++row)(*hessian)(row,col)=dP.data()[row];
    }
    *hessian=(0.5*(*hessian+hessian->transpose())).eval();
}
__host__ __device__ __forceinline__ bool plastic_commit(const Matrix3x3& trial,
    Matrix3x3& inverse,Float& alpha,Float mu,Float lambda,Float yield,Float hardening) {
    const PlasticResult r=plastic_trial(trial,mu,lambda,alpha,yield,hardening);
    if(!r.valid || !r.active)return false;
    Matrix3x3 next=inverse*r.V*(-r.plastic_log.array()).exp().matrix().asDiagonal()*r.V.transpose();
    const Float det=next.determinant();
    if(!(det>1e-12)||!isfinite(det))return false;
    next/=cbrt(det);inverse=next;alpha+=r.increment;return true;
}
}
