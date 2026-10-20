// Commit in update_state after Newton exits; trial evaluations never mutate history.
// advance_ipc rejects iteration-limit and line-search failures before this hook.
// Failed worlds must be reset; this does not provide full-frame rollback.
namespace {
__global__ void volume_plastic_update(cuda_tool::CBufferView<Vector4i> indices,
    cuda_tool::CBufferView<Vector3> xs,cuda_tool::CBufferView<Matrix3x3> Dm_invs,
    cuda_tool::BufferView<Matrix3x3> inverse,cuda_tool::BufferView<Float> accumulated,
    cuda_tool::CBufferView<Float> mus,cuda_tool::CBufferView<Float> lambdas,
    cuda_tool::CBufferView<Float> yield,cuda_tool::CBufferView<Float> hardening,
    cuda_tool::CBufferView<IndexT> enabled,int n) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if(i>=n || !enabled(i))return;
    auto t=indices(i);
    const Matrix3x3 trial=fem::F(xs(t[0]),xs(t[1]),xs(t[2]),xs(t[3]),Dm_invs(i))*inverse(i);
    const auto r=plastic_trial(trial,mus(i),lambdas(i),accumulated(i),yield(i),hardening(i));
    if(!r.valid){printf("J2 accepted-state return failed for tet %d\n",i);asm("trap;");return;}
    if(!r.active)return;
    Matrix3x3 next=inverse(i)*r.V*(-r.plastic_log.array()).exp().matrix().asDiagonal()*r.V.transpose();
    const Float det=next.determinant();
    if(!(det>1e-12)||!isfinite(det)){printf("J2 invalid plastic state for tet %d\n",i);asm("trap;");return;}
    inverse(i)=next/cbrt(det);accumulated(i)+=r.increment;
}
}
class VolumePlasticityIntegrator final : public TimeIntegrator {
public:
    using TimeIntegrator::TimeIntegrator;
    SimSystemSlot<StableNeoHookean3D> material;
    SimSystemSlot<FiniteElementMethod> fem;
    BufferDump inverse_dump,accumulated_dump;
    void do_build(TimeIntegrator::BuildInfo&) override {
        material=require<StableNeoHookean3D>();fem=require<FiniteElementMethod>();
    }
    void do_init(TimeIntegrator::InitInfo&) override {}
    void do_predict_dof(TimeIntegrator::PredictDofInfo&) override {}
    void do_update_state(TimeIntegrator::UpdateVelocityInfo&) override {
        if(!material->has_plasticity)return;
        FEM3DConstitution::BaseInfo b{material.operator->(),0,0.0};
        int n=static_cast<int>(material->plastic_inverse.size());
        if(!n)return;
        auto k=volume_plastic_update;
        k<<<cuda_tool::best_grid_dim(n,k),cuda_tool::best_block_dim(k)>>>(
            b.indices(),fem->xs(),b.Dm_invs(),material->plastic_inverse.view(),
            material->plastic_accumulated.view(),material->mus.cview(),material->lambdas.cview(),
            material->plastic_yield.cview(),material->plastic_hardening.cview(),material->plastic_enabled.cview(),n);
    }
    bool do_dump(DumpInfo& info) override {
        auto p=info.dump_path(UIPC_RELATIVE_SOURCE_FILE);auto f=info.frame();
        return inverse_dump.dump(fmt::format("{}plastic_inverse.{}",p,f),material->plastic_inverse)
            && accumulated_dump.dump(fmt::format("{}plastic_accumulated.{}",p,f),material->plastic_accumulated);
    }
    bool do_try_recover(RecoverInfo& info) override {
        auto p=info.dump_path(UIPC_RELATIVE_SOURCE_FILE);auto f=info.frame();
        return inverse_dump.load(fmt::format("{}plastic_inverse.{}",p,f))
            && accumulated_dump.load(fmt::format("{}plastic_accumulated.{}",p,f));
    }
    void do_apply_recover(RecoverInfo&) override {
        inverse_dump.apply_to(material->plastic_inverse);accumulated_dump.apply_to(material->plastic_accumulated);
    }
    void do_clear_recover(RecoverInfo&) override {inverse_dump.clean_up();accumulated_dump.clean_up();}
};
REGISTER_SIM_SYSTEM(VolumePlasticityIntegrator);
