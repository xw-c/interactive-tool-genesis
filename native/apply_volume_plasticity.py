"""Apply the optional CUDA overlay to an isolated libuipc checkout, never site-packages."""
import argparse,ast
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent

def validate_overlay(folder):
    header=(folder/'plasticity.h').read_text(encoding='utf-8')
    if any(token not in header for token in ('struct PlasticResult','plastic_trial(','plastic_derivatives(')) or 'plastic_return(' in header:
        raise RuntimeError('Incompatible v2 plasticity.h; use the matching variational J2 source')

def patch_step_commit(text):
    """Reject failed solves before any time integrator commits material history."""
    replacements = [
        ('    constexpr bool AbortOnException = uipc::RUNTIME_CHECK;\n\n    auto pipeline = [&]() noexcept(AbortOnException)',
         '    auto pipeline = [&]()'),
        ('                    // Check Line Search Iteration: report warnings or throw exceptions if needed',
         '                    if(line_search_iter >= m_line_searcher->max_iter() || !std::isfinite(last_E))\n'
         '                        throw SimEngineException("Line search failed; reset this simulation");\n'
         '                    // Check Line Search Iteration: report warnings or throw exceptions if needed'),
        ('            // 5. Update Velocity => v = (x - x_0) / dt',
         '            if(newton_iter >= m_newton_max_iter->view()[0])\n'
         '                throw SimEngineException("Newton iteration limit reached; reset this simulation");\n'
         '            check_newton_iter(newton_iter);\n\n'
         '            // 5. Update Velocity => v = (x - x_0) / dt'),
        ('            // Check Newton Iteration\n            // report warnings or throw exceptions if needed\n            check_newton_iter(newton_iter);', ''),
    ]
    for old,new in replacements:
        if text.count(old)!=1:raise RuntimeError('Unsupported step-commit source layout: '+old[:80])
        text=text.replace(old,new)
    return text

def apply(root):
    validate_overlay(HERE)  # Before writing anything to the source snapshot.
    folder=root/'src/backends/cuda/finite_element/constitutions'
    path=folder/'stable_neo_hookean_3d.cu'
    source=path.read_text(encoding='utf-8')
    if 'interactive_ipc_plasticity_version' in source:
        raise RuntimeError('Overlay already applied; start from an unmodified source checkout')
    def replace(old,new,count=1):
        nonlocal source
        if source.count(old)!=count:raise RuntimeError('Unsupported libuipc source layout: '+old[:70])
        source=source.replace(old,new)
    replace('#include <finite_element/constitutions/stable_neo_hookean_3d_function.h>', '#include "snk1.h"')
    replace('#include <kernel_cout.h>', '#include <kernel_cout.h>\n#include "plasticity.h"\n#include <time_integrator/time_integrator.h>\n#include <utils/dump_utils.h>')
    replace('        cuda_tool::CBufferView<Matrix3x3> Dm_invs,', '        cuda_tool::CBufferView<Matrix3x3> Dm_invs,\n        cuda_tool::CBufferView<Matrix3x3> plastic_inverse,')
    replace('        cuda_tool::CBufferView<Matrix3x3>      Dm_invs,', '        cuda_tool::CBufferView<Matrix3x3>      Dm_invs,\n        cuda_tool::CBufferView<Matrix3x3>      plastic_inverse,')
    replace('        const Matrix3x3& Dm_inv = Dm_invs(I);', '        const Matrix3x3 Dm_inv = Dm_invs(I) * plastic_inverse(I);',2)
    replace('                info.Dm_invs(),', '                info.Dm_invs(),\n                plastic_inverse.cview(),',2)
    # Trial return is evaluated at EVERY energy/gradient/Hessian evaluation.
    # Failed solves stop before update_state commits plastic history.
    replace('        cuda_tool::CBufferView<Matrix3x3> plastic_inverse,',
        '        cuda_tool::CBufferView<Matrix3x3> plastic_inverse,\n        cuda_tool::CBufferView<Float> plastic_accumulated, cuda_tool::CBufferView<Float> plastic_yield, cuda_tool::CBufferView<Float> plastic_hardening,\n        cuda_tool::CBufferView<IndexT> plastic_enabled,')
    replace('        cuda_tool::CBufferView<Matrix3x3>      plastic_inverse,',
        '        cuda_tool::CBufferView<Matrix3x3>      plastic_inverse,\n        cuda_tool::CBufferView<Float> plastic_accumulated, cuda_tool::CBufferView<Float> plastic_yield, cuda_tool::CBufferView<Float> plastic_hardening,\n        cuda_tool::CBufferView<IndexT> plastic_enabled,')
    replace('                plastic_inverse.cview(),',
        '                plastic_inverse.cview(),\n                plastic_accumulated.cview(), plastic_yield.cview(), plastic_hardening.cview(),\n                plastic_enabled.cview(),',2)
    replace('        SNH::E(E, mu, lambda, F);',
        '        if(plastic_enabled(I)) E=plastic_trial(F,mu,lambda,plastic_accumulated(I),plastic_yield(I),plastic_hardening(I)).energy;\n        else SNH::E(E, mu, lambda, F);')
    replace('        SNH::dEdVecF(dEdF, mu, lambda, F);',
        '        PlasticResult trial;\n        if(plastic_enabled(I)) {\n            trial=plastic_trial(F,mu,lambda,plastic_accumulated(I),plastic_yield(I),plastic_hardening(I));\n            if(!trial.valid) {printf("J2 local return failed for tet %d\\n",I);asm("trap;");}\n            plastic_derivatives(F,mu,lambda,trial,dEdF,nullptr);\n        } else SNH::dEdVecF(dEdF, mu, lambda, F);')
    replace('        SNH::ddEddVecF(ddEddF, mu, lambda, F);',
        '        if(plastic_enabled(I)) plastic_derivatives(F,mu,lambda,trial,dEdF,&ddEddF);\n        else SNH::ddEddVecF(ddEddF, mu, lambda, F);')
    replace('    virtual U64 get_uid()', '''    bool has_plasticity = false;
    cuda_tool::DeviceBuffer<Matrix3x3> plastic_inverse;
    cuda_tool::DeviceBuffer<Float> plastic_accumulated, plastic_yield, plastic_hardening;
    cuda_tool::DeviceBuffer<IndexT> plastic_enabled;

    virtual U64 get_uid()''')
    replace('        h_mus.resize(N);', '''        vector<Matrix3x3> inverse(N, Matrix3x3::Identity());
        vector<Float> zero(N, 0.0), yields(N, 500.0), hardening(N, 0.0);
        vector<IndexT> enabled(N, 0);
        info.for_each(geo_slots,
            [](geometry::SimplicialComplex& sc) {
                auto e=sc.tetrahedra().find<IndexT>("plasticity_enabled");
                if(!e)e=sc.tetrahedra().create<IndexT>("plasticity_enabled",0);
                auto y=sc.tetrahedra().find<Float>("plastic_yield_stress");
                if(!y)y=sc.tetrahedra().create<Float>("plastic_yield_stress",500.0);
                auto h=sc.tetrahedra().find<Float>("plastic_hardening");
                if(!h)h=sc.tetrahedra().create<Float>("plastic_hardening",0.0);
                return zip(e->view(),y->view(),h->view());
            },
            [&](const ForEachInfo& I, auto input) {
                auto [e,y,h]=input;
                auto j=I.global_index();
                enabled[j]=e;yields[j]=y;hardening[j]=h;
                UIPC_ASSERT(std::isfinite(y) && y>=0,
                            "plastic_yield_stress must be finite and nonnegative");
                UIPC_ASSERT(std::isfinite(h) && h>=0,
                            "plastic_hardening must be finite and nonnegative");
                has_plasticity |= e!=0;
            });
        plastic_inverse.resize(N);plastic_inverse.view().copy_from(inverse.data());
        plastic_accumulated.resize(N);plastic_accumulated.view().copy_from(zero.data());
        plastic_yield.resize(N);plastic_yield.view().copy_from(yields.data());
        plastic_hardening.resize(N);plastic_hardening.view().copy_from(hardening.data());
        plastic_enabled.resize(N);plastic_enabled.view().copy_from(enabled.data());
        h_mus.resize(N);''')
    replace('REGISTER_SIM_SYSTEM(StableNeoHookean3D);', 'REGISTER_SIM_SYSTEM(StableNeoHookean3D);\n#include "plasticity_integrator.inl"')
    source += '''
#ifdef _WIN32
#define IPC_PLASTIC_EXPORT __declspec(dllexport)
#else
#define IPC_PLASTIC_EXPORT __attribute__((visibility("default")))
#endif
extern "C" IPC_PLASTIC_EXPORT int interactive_ipc_plasticity_version() { return 2; }
extern "C" IPC_PLASTIC_EXPORT int interactive_ipc_step_commit_version() { return 1; }
'''
    path.write_text(source,encoding='utf-8')
    for file in ['plasticity.h','plasticity_integrator.inl','snk1.h']:shutil.copyfile(HERE/file,folder/file)
    # The source snapshot predates the wheel's split between min_iter and K_min.
    # Preserve the running tool's Newton defaults instead of changing its accuracy.
    cfg=root/'src/core/core/scene_default_config.cpp'
    text=cfg.read_text(encoding='utf-8')
    text=text.replace('"newton/min_iter", IndexT{1}', '"newton/min_iter", IndexT{0}')
    text=text.replace('"newton/semi_implicit/enable", IndexT{0}', '"newton/semi_implicit/enable", IndexT{1}')
    text=text.replace('    config.create("newton/semi_implicit/beta_tol"',
        '    config.create("newton/semi_implicit/K_min", IndexT{6});\n    config.create("newton/semi_implicit/beta_tol"')
    cfg.write_text(text,encoding='utf-8')
    advance=root/'src/backends/cuda/engine/advance_ipc.cu'
    text=advance.read_text(encoding='utf-8')
    text=text.replace('auto k_min = m_newton_min_iter->view()[0];',
        'auto k_min = world().scene().config().find<IndexT>("newton/semi_implicit/K_min")->view()[0];')
    advance.write_text(patch_step_commit(text),encoding='utf-8')
    # CUDA 12 exposes the edge-data signature under _v2; CUDA 13 uses the main name.
    graph=root/'src/backends/cuda/cuda_tool/graph.h'
    text=graph.read_text(encoding='utf-8')
    call='        err = cudaGraphAddNode(&while_node, m_graph, &setup_node, nullptr, 1, &params);'
    text=text.replace(call, '#if CUDART_VERSION < 13000\n'+call.replace('cudaGraphAddNode(', 'cudaGraphAddNode_v2(')+'\n#else\n'+call+'\n#endif')
    graph.write_text(text,encoding='utf-8')
    # Upstream's developer post-build hook installs into the selected interpreter.
    # This extension must remain project-local, including when wheel mode is off.
    hook=root/'scripts/after_build_pyuipc.py'
    text=hook.read_text(encoding='utf-8')
    text=text.replace('    uninstall_package()\n','    print("Keeping the installed uipc package unchanged.")\n')
    text=text.replace('        install_package(binary_dir)\n','        print("Project-local package only; automatic installation disabled.")\n')
    text=text.replace("print(f'Installing the package to Python Environment: {sys.executable}')",
                      "print('Keeping the generated package project-local.')")
    text=text.replace('sys.path.append(str(typings_dir))','sys.path.insert(0, str(typings_dir))')
    if any(isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'uninstall_package','install_package'} for node in ast.walk(ast.parse(text))):
        raise RuntimeError('Unable to disable upstream environment mutation')
    hook.write_text(text,encoding='utf-8')
    # Match the installed wheel: thickness inputs are one-sided radii (h=2r),
    # and DSB's kappa*L/h*angle^2 is already a hinge energy, not an area density.
    membrane=root/'src/constitution/strain_limiting_baraff_witkin.cpp'
    text=membrane.read_text(encoding='utf-8')
    text=text.replace('(stretch_moduli.lambda() + 2 * stretch_moduli.mu()) * thickness;',
                      '(stretch_moduli.lambda() + 2 * stretch_moduli.mu()) * (2 * thickness);')
    membrane.write_text(text,encoding='utf-8')
    for name in ['discrete_shell_bending_function.h','strain_plastic_discrete_shell_bending_function.h']:
        header=folder/name
        text=header.read_text(encoding='utf-8').replace('V_bar = A;', 'V_bar = 1.0; // hinge energy already includes the geometric weight')
        text=text.replace('Shell element weight is the AREA:', 'The dihedral formula already includes the geometric weight:')
        header.write_text(text,encoding='utf-8')
    bending=root/'src/constitution/discrete_shell_bending.cpp'
    text=bending.read_text(encoding='utf-8')
    text=text.replace('return young_modulus * thickness * thickness * thickness',
                      'return young_modulus * (2 * thickness) * (2 * thickness) * (2 * thickness)')
    text=text.replace('(t_view[edges[i](0)] + t_view[edges[i](1)]) * 0.5;',
                      '(t_view[edges[i](0)] + t_view[edges[i](1)]);')
    bending.write_text(text,encoding='utf-8')





if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    apply(parser.parse_args().source.resolve())
