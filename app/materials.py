"""Element material overrides on one connected libuipc mesh."""
import numpy as np
from .config import Material


def resolve_materials(config):
    base=config.material.model_dump()
    return [config.material]+[Material.model_validate(base | r.material.model_dump(exclude_unset=True))
                             for r in config.material_regions]


def element_material_ids(config,count):
    ids=np.zeros(count,dtype=np.int32)
    for i,region in enumerate(config.material_regions,1):
        elements=np.asarray(region.elements,dtype=np.int64)
        if np.any(elements>=count):raise ValueError(f'材质区域 {i} 的单元编号超出范围 [0, {count})')
        if len(np.unique(elements))!=len(elements) or np.any(ids[elements]!=0):
            raise ValueError(f'材质区域 {i} 有重复或重叠的单元编号')
        ids[elements]=i
    return ids


def write_attribute(collection,name,values,integer=False):
    import uipc
    attr=collection.find(name)
    if attr is None:attr=collection.create(name,0 if integer else 0.)
    uipc.view(attr).reshape(-1)[:]=values


def configure_elements(geometry,body_type,materials,ids,bending=None):
    cells=geometry.triangles() if body_type=='shell' else geometry.tetrahedra()
    topology=np.asarray(cells.topo().view()).reshape(-1,3 if body_type=='shell' else 4)
    x=np.asarray(geometry.positions().view()).reshape(-1,3)
    values=lambda name:np.asarray([getattr(m,name) for m in materials])[ids]
    E=values('youngs_modulus_pa');nu=values('poisson_ratio');rho=values('density_kg_m3')
    if body_type=='shell':
        area=np.linalg.norm(np.cross(x[topology[:,1]]-x[topology[:,0]],x[topology[:,2]]-x[topology[:,0]]),axis=1)/2
        thickness=values('thickness_m')
        measure=area*thickness
        write_attribute(cells,'lambda',E*thickness/(1-nu**2))
        write_attribute(cells,'mu',values('shear_stiffness_n_m'))
        # Keep Genesis's exact uniform collision radius. This native backend
        # asserts equal endpoint radii; element thickness affects membrane/mass.
        if bending is not None:configure_bending(geometry,materials,ids,area,bending)
    else:
        mu=E/(2*(1+nu));lam=E*nu/((1+nu)*(1-2*nu))
        # Preserve Genesis/libuipc's Stable Neo-Hookean coefficient conversion.
        write_attribute(cells,'mu',4*mu/3)
        write_attribute(cells,'lambda',lam+5*mu/6)
        a=x[topology[:,1:]]-x[topology[:,0,None]]
        measure=np.abs(np.linalg.det(a))/6
        if any(m.volume_plasticity for m in materials):
            write_attribute(cells,'plasticity_enabled',values('volume_plasticity'),integer=True)
            write_attribute(cells,'plastic_yield_stress',4*mu*values('volume_yield_strain_percent')/100)
            write_attribute(cells,'plastic_hardening',4*mu*values('volume_plastic_hardening'))
    # Exact lumped mass from each cell; never average Young's modulus at vertices.
    mass=np.zeros(len(x))
    for j in range(topology.shape[1]):np.add.at(mass,topology[:,j],measure*rho/topology.shape[1])
    write_attribute(geometry.vertices(),'mass',mass)


def configure_bending(geometry,materials,ids,areas,bending):
    plastic=any(m.bending_model and m.bending_plasticity for m in materials)
    if plastic:bending.apply_to(geometry,0.,0.,0.)
    else:bending.apply_to(geometry,0.)
    faces=np.asarray(geometry.triangles().topo().view()).reshape(-1,3)
    edges=np.asarray(geometry.edges().topo().view()).reshape(-1,2)
    lookup={tuple(sorted(e)):i for i,e in enumerate(edges.tolist())}
    total=np.zeros(len(edges));stiffness=np.zeros(len(edges))
    thresholds=np.zeros(len(edges));hardening=np.zeros(len(edges));elastic=np.zeros(len(edges),dtype=bool)
    for fi,face in enumerate(faces):
        m=materials[ids[fi]];weight=areas[fi]
        for a,b in ((0,1),(1,2),(2,0)):
            ei=lookup[tuple(sorted((int(face[a]),int(face[b]))))]
            total[ei]+=weight
            stiffness[ei]+=weight*(m.bending_rigidity_nm if m.bending_model else 0.)
            thresholds[ei]+=weight*np.deg2rad(m.bending_yield_angle_deg)
            hardening[ei]+=weight*m.bending_plastic_hardening
            elastic[ei]|=not (m.bending_model and m.bending_plasticity)
    for array in (stiffness,thresholds,hardening):np.divide(array,total,out=array,where=total>0)
    write_attribute(geometry.edges(),'bending_stiffness',stiffness)
    if plastic:
        # A fixed rest angle and current angle are both in [-pi, pi]. This exact
        # upper bound prevents yielding on elastic/mixed edges without infinity.
        thresholds[elastic]=2*np.pi
        hardening[elastic]=0.
        write_attribute(geometry.edges(),'bending_yield_threshold',thresholds)
        write_attribute(geometry.edges(),'bending_hardening_modulus',hardening)
