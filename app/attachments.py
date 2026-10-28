"""Attach existing native geometries; all topology changes precede World.init."""
import numpy as np
from scipy.spatial import cKDTree
import uipc
from uipc.constitution import AffineBodyConstitution, AffineBodyFixedJoint, SoftVertexStitch


def _is_abd(slot):
    geometry = slot.geometry()
    if geometry.instances().size() != 1:
        raise ValueError('Split instanced geometries before attaching them')
    uid = geometry.meta().find('constitution_uid')
    if uid is None:
        raise ValueError('Apply a native body constitution before attachment')
    return int(np.asarray(uipc.view(uid)).ravel()[0]) == AffineBodyConstitution().uid()


def stitch_vertices(scene, name, left, right, pairs, stiffness_n_m, rest_length_m=0.):
    """Connect native vertex IDs on two ends (possibly one body); stiffness is per pair."""
    _is_abd(left)
    _is_abd(right)
    pairs = np.asarray(pairs)
    if pairs.ndim != 2 or pairs.shape[1] != 2 or not len(pairs) or pairs.dtype.kind not in 'iu':
        raise ValueError('Expected nonempty integer (N,2) vertex pairs')
    for index, slot in enumerate((left, right)):
        if pairs[:, index].min() < 0 or pairs[:, index].max() >= slot.geometry().vertices().size():
            raise ValueError('Attachment vertex ID out of range')
    same_body = left.id() == right.id()
    if same_body and np.any(pairs[:, 0] == pairs[:, 1]):
        raise ValueError('A vertex cannot be stitched to itself')
    if len(np.unique(np.sort(pairs, axis=1) if same_body else pairs, axis=0)) != len(pairs):
        raise ValueError('Repeated vertex pairs multiply stiffness')
    values = []
    for value, positive in ((stiffness_n_m, True), (rest_length_m, False)):
        array = np.broadcast_to(np.asarray(value, float), (len(pairs),))
        if not np.isfinite(array).all() or np.any(array <= 0 if positive else array < 0):
            raise ValueError('Invalid stiffness or rest length')
        values.append(array)
    geometry = SoftVertexStitch().create_geometry(
        [left, right], pairs.astype(np.int32), float(values[0][0]), float(values[1][0]))
    stored = np.asarray(uipc.view(geometry.instances().find('topo'))).reshape(-1, 2)
    # Older Python bindings can lose the temporary index buffer. The native
    # attribute is authoritative; copy the owned array explicitly before World.init.
    stored[:] = pairs
    if not np.array_equal(stored, pairs):
        raise RuntimeError('libuipc stitch binding corrupted indices; install the validated native package')
    for key, array in zip(('kappa', 'rest_length'), values):
        uipc.view(geometry.instances().find(key)).reshape(-1)[:] = array
    return scene.objects().create(name).geometries().create(geometry)[0]


def locate_tets(points, vertices, cells):
    """Locate embedded points without remeshing; reject extrapolation."""
    corners = vertices[cells]
    origin = corners[:, 3]
    inverse = np.linalg.inv(np.stack([corners[:, i]-origin for i in range(3)], axis=-1))
    tree = cKDTree(corners.mean(axis=1))
    result = []
    for point in points:
        for count in (32, 128, len(cells)):
            candidates = np.atleast_1d(tree.query(point, k=min(count, len(cells)))[1])
            abc = np.einsum('nij,nj->ni', inverse[candidates], point-origin[candidates])
            weights = np.column_stack((abc, 1-abc.sum(axis=1)))
            violation = np.maximum(-weights, 0).sum(axis=1)
            best = violation.argmin()
            if violation[best] <= 1e-6:
                result.append(candidates[best])
                break
        else:
            raise ValueError('Attachment point lies outside its FEM tetrahedra')
    return np.asarray(result, np.int32)


def locate_elements(points, vertices, cells):
    """Locate shell samples on triangles or volume samples inside tetrahedra."""
    if cells.shape[1] == 4:
        return locate_tets(points, vertices, cells)
    corners = vertices[cells]
    edges = corners[:, 1:]-corners[:, :1]
    gram = edges @ edges.transpose(0, 2, 1)
    tree = cKDTree(corners.mean(axis=1))
    result = []
    for point in points:
        for count in (32, 128, len(cells)):
            candidates = np.atleast_1d(tree.query(point, k=min(count, len(cells)))[1])
            delta = point-corners[candidates, 0]
            uv = np.linalg.solve(gram[candidates], np.einsum('nij,nj->ni', edges[candidates], delta)[..., None])[..., 0]
            weights = np.column_stack((1-uv.sum(axis=1), uv))
            distance = np.linalg.norm(delta-np.einsum('ni,nij->nj', uv, edges[candidates]), axis=1)
            valid = np.flatnonzero((weights.min(axis=1) >= -1e-6) & (distance <= 1e-6))
            if len(valid):
                result.append(candidates[valid[np.argmin(distance[valid])]])
                break
        else:
            raise ValueError('Attachment point lies outside its shell triangles')
    return np.asarray(result, np.int32)


def _resolve_points(slot, points, tolerance_m, vertex_ids=None):
    points = np.asarray(points, float)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Expected finite nonempty (N,3) world points')
    geometry = slot.geometry()
    vertices = np.asarray(uipc.view(geometry.positions())).reshape(-1, 3)
    if vertex_ids is not None:
        ids = np.asarray(vertex_ids)
        if (ids.ndim != 1 or len(ids) != len(points) or ids.dtype.kind not in 'iu'
                or ids.min() < 0 or ids.max() >= len(vertices)):
            raise ValueError('Explicit attachment vertex IDs are invalid')
        return ids.astype(np.int32), np.zeros(len(ids)), None
    if _is_abd(slot):
        transform = np.asarray(uipc.view(geometry.transforms()))[0]
        local = (points-transform[:3, 3]) @ np.linalg.inv(transform[:3, :3]).T
        return np.arange(len(vertices), len(vertices)+len(points)), np.zeros(len(points)), local
    distances, ids = cKDTree(vertices).query(points)
    if distances.max() > tolerance_m:
        raise ValueError(f'FEM endpoint misses existing nodes by {distances.max():.6g} m; '
                         'increase projection_tolerance_m explicitly to permit an approximation')
    return ids, distances, None


def stitch_points(scene, name, left, right, points0, points1, stiffness_n_m,
                  projection_tolerance_m=1e-8, *, vertex_ids0=None, vertex_ids1=None):
    """Resolve world points to FEM nodes or ABD references, then register a stitch.

    Only native runtime data is returned: (geometry slot, two projection-error arrays).
    ABD references are not surface vertices and do not alter closed-mesh mass integration.
    """
    if not np.isfinite(projection_tolerance_m) or projection_tolerance_m < 0:
        raise ValueError('Projection tolerance must be finite and nonnegative')
    resolved = [_resolve_points(slot, points, projection_tolerance_m, ids)
                for slot, points, ids in ((left, points0, vertex_ids0), (right, points1, vertex_ids1))]
    if len(resolved[0][0]) != len(resolved[1][0]):
        raise ValueError('Attachment point arrays need equal lengths')
    if left.id() == right.id() and resolved[0][2] is not None and resolved[1][2] is not None:
        resolved[1][0][:] += len(resolved[0][0])
    resized = []
    try:
        for slot, (_, _, local) in zip((left, right), resolved):
            if local is not None:
                geometry = slot.geometry()
                count = geometry.vertices().size()
                geometry.vertices().resize(count+len(local))
                resized.append((geometry, count))
                uipc.view(geometry.positions()).reshape(-1, 3)[count:] = local
                uipc.view(geometry.vertices().find('is_surf')).reshape(-1)[count:] = 0
        pairs = np.column_stack([item[0] for item in resolved])
        slot = stitch_vertices(scene, name, left, right, pairs, stiffness_n_m)
    except Exception:
        for geometry, count in reversed(resized):
            geometry.vertices().resize(count)
        raise
    return slot, (resolved[0][1], resolved[1][1])


def fixed_joint(scene, name, left, right, strength_ratio=1e6):
    """Preserve the assembled relative pose of two ABD instances."""
    if left.id() == right.id() or not _is_abd(left) or not _is_abd(right):
        raise ValueError('Fixed joint requires two distinct ABD geometries')
    if not np.isfinite(strength_ratio) or strength_ratio <= 0:
        raise ValueError('Expected positive fixed-joint strength')
    geometry = AffineBodyFixedJoint().create_geometry(
        [left], np.array([0], np.int32), [right], np.array([0], np.int32), np.array([strength_ratio]))
    return scene.objects().create(name).geometries().create(geometry)[0]
