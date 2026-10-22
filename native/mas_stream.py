"""Make the bundled MAS apply kernels honor the fused-PCG CUDA stream.

Incremental Windows build against this workspace's existing plasticity build.
Produces a separate complete package; does not modify the original package.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def overlay(source,dest):
    dest.mkdir(parents=True,exist_ok=True)
    for name in ('mas_preconditioner_engine.h','mas_preconditioner_engine.cu','fem_mas_preconditioner.cu'):
        s=(source/name).read_text(encoding='utf8')
        if name.startswith('mas_preconditioner_engine'):
            # Only the public apply pipeline and its three helpers take a stream.
            if name.endswith('.h'):
                for method in ('apply','build_multi_level_R','schwarz_local_solve','collect_final_Z'):
                    start=s.index('    void '+method+'(');end=s.index(';',start)
                    part=s[start:end]
                    assert part.endswith('converged)'),part
                    s=s[:start]+part[:-1]+', cudaStream_t stream = nullptr)'+s[end:]
            else:
                start=s.index('void MASPreconditionerEngine::build_multi_level_R(')
                end=s.index('void MASPreconditionerEngine::dump_cluster_matrices_debug(',start)
                part=s[start:end]
                assert part.count('cuda_tool::CVarView<IndexT> converged)')==4
                part=part.replace('cuda_tool::CVarView<IndexT> converged)',
                    'cuda_tool::CVarView<IndexT> converged, cudaStream_t stream)')
                assert part.count('0, nullptr>>>')==3
                part=part.replace('0, nullptr>>>','0, stream>>>')
                part=part.replace('multi_level_R.resize(m_total_num_clusters);','multi_level_R.resize(m_total_num_clusters, stream);')
                part=part.replace('multi_level_Z.resize(m_total_num_clusters);','multi_level_Z.resize(m_total_num_clusters, stream);')
                old='multi_level_R\n            .view(m_total_map_nodes, m_total_num_clusters - m_total_map_nodes)\n            .fill(Eigen::Vector3f::Zero());'
                assert old in part
                part=part.replace(old,'multi_level_R.fill(multi_level_R.view(m_total_map_nodes, m_total_num_clusters - m_total_map_nodes), Eigen::Vector3f::Zero(), stream);')
                part=part.replace('multi_level_Z.view(0, m_total_num_clusters).fill(float3{0, 0, 0});',
                    'multi_level_Z.fill(multi_level_Z.view(0, m_total_num_clusters), float3{0, 0, 0}, stream);')
                part=part.replace('build_multi_level_R(r, converged);','build_multi_level_R(r, converged, stream);')
                part=part.replace('schwarz_local_solve(converged);','schwarz_local_solve(converged, stream);')
                part=part.replace('collect_final_Z(z, converged);','collect_final_Z(z, converged, stream);')
                s=s[:start]+part+s[end:]
        else:
            old='SizeT                       num_verts)'
            assert old in s
            s=s.replace(old,'SizeT                       num_verts, cudaStream_t stream)')
            old='cuda_tool::best_block_dim(k), 0, nullptr>>>(\n            r, z'
            assert old in s
            s=s.replace(old,'cuda_tool::best_block_dim(k), 0, stream>>>(\n            r, z')
            s=s.replace('engine.apply(info.r(), info.z(), converged);','engine.apply(info.r(), info.z(), converged, info.stream());')
            s=s.replace('diag_inv.size());','diag_inv.size(), info.stream());')
            s+='\nextern "C" __declspec(dllexport) int interactive_ipc_mas_stream_version() { return 1; }\n'
        (dest/name).write_text(s,encoding='utf8')


def build():
    out=ROOT/'runtime/mas-stream';out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'runtime/plasticity-build/src/src/backends/cuda/finite_element'
    overlay(source,out/'source/finite_element')
    base=ROOT/'runtime/plasticity-only'
    lines=(base/'build.cmd').read_text().splitlines()
    compile_template=next(s for s in lines if ' -c ' in s)
    prefix=compile_template.split(' -c ',1)[0]
    include=' -I'+str(out/'source')
    first=prefix.index(' -I')
    prefix=prefix[:first]+include+prefix[first:]
    commands=lines[:3]
    for name in ('fem_mas_preconditioner','mas_preconditioner_engine'):
        flags=prefix.replace('src/backends/cuda/engine/advance_ipc.cu','src/backends/cuda/finite_element/'+name+'.cu')
        commands += [flags+' -c '+str(out/f'source/finite_element/{name}.cu')+' -o '+str(out/f'{name}.cu.obj'),'if errorlevel 1 exit /b 1']
    for response in ('device','host'):
        s=(base/f'{response}.rsp').read_text()
        for name in ('fem_mas_preconditioner','mas_preconditioner_engine'):
            old='src\\backends\\cuda\\CMakeFiles\\cuda.dir\\finite_element\\'+name+'.cu.obj'
            assert old in s
            s=s.replace(old,str(out/f'{name}.cu.obj'))
        s=s.replace(str(base/'device.obj'),str(out/'device.obj'))
        (out/f'{response}.rsp').write_text(s)
    for s in lines:
        if ' -dlink ' in s or '/nologo @' in s:
            commands += [s.replace(str(base),str(out)),'if errorlevel 1 exit /b 1']
    script=out/'build.cmd';script.write_text('\n'.join(commands)+'\n')
    subprocess.run(['cmd','/c',str(script)],check=True)
    package=out/'package'
    (package/'mas-stream-validated.json').unlink(missing_ok=True)
    shutil.copytree(base/'package',package,dirs_exist_ok=True)
    target=package/'uipc/_native/uipc_backend_cuda.dll'
    shutil.copy2(out/'uipc_backend_cuda.dll',target)
    info=dict(mas_stream_version=1,base_package='runtime/plasticity-only/package',
        cuda_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        source_files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (out/'source/finite_element').iterdir()})
    (package/'mas-stream-build-info.json').write_text(json.dumps(info,indent=2),encoding='utf8')
    plasticity_info=package/'plasticity-only-build-info.json'
    record=json.loads(plasticity_info.read_text());record['cuda_sha256']=info['cuda_sha256']
    record['mas_stream_version']=1
    plasticity_info.write_text(json.dumps(record,indent=2),encoding='utf8')
    print('Ready:',package,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args();build()
