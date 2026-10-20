#pragma once
// SNK1 polynomial variant used by the installed pyuipc 0.0.28 CUDA wheel.
// Keep the elastic energy consistent while adding F = Fe Fp.
#include <type_define.h>
#include <Eigen/Geometry>
namespace uipc::backend::cuda::sym::stable_neo_hookean_3d {
template<class T> __host__ __device__ void E(T& out,const T& mu,const T& lambda,const Eigen::Matrix<T,3,3>& F) {
    T j=F.determinant()-1;
    out=T(.5)*mu*(F.squaredNorm()-3)+T(.5)*lambda*j*j-mu*j;
}
template<class T> __host__ __device__ Eigen::Matrix<T,3,3> cofactor(const Eigen::Matrix<T,3,3>& F) {
    Eigen::Matrix<T,3,3> C;
    C.col(0)=F.col(1).cross(F.col(2));C.col(1)=F.col(2).cross(F.col(0));C.col(2)=F.col(0).cross(F.col(1));
    return C;
}
template<class T> __host__ __device__ void dEdVecF(Eigen::Matrix<T,3,3>& out,const T& mu,const T& lambda,const Eigen::Matrix<T,3,3>& F) {
    out=mu*F+(lambda*(F.determinant()-1)-mu)*cofactor(F);
}
__host__ __device__ inline int epsilon3(int a,int b,int c) {return ((a-b)*(b-c)*(c-a))/2;}
template<class T> __host__ __device__ void ddEddVecF(Eigen::Matrix<T,9,9>& H,const T& mu,const T& lambda,const Eigen::Matrix<T,3,3>& F) {
    Eigen::Matrix<T,3,3> C=cofactor(F);
    T scale=lambda*(F.determinant()-1)-mu;
    for(int j=0;j<3;++j)for(int i=0;i<3;++i)for(int l=0;l<3;++l)for(int k=0;k<3;++k) {
        T value=lambda*C(i,j)*C(k,l);
        if(i==k && j==l)value+=mu;
        if(i!=k && j!=l) {int m=3-i-k,n=3-j-l;value+=scale*epsilon3(i,k,m)*epsilon3(j,l,n)*F(m,n);}
        H(i+3*j,k+3*l)=value;
    }
}
}
