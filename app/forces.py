"""Distribute a resultant force over a patch of the reference surface."""
import numpy as np


def surface_vertex_areas(positions, triangles):
    """One third of each reference triangle's area belongs to each vertex."""
    points = positions[triangles]
    edges = points[:, 1:] - points[:, :1]
    areas = np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1) / 6
    weights = np.zeros(len(positions))
    np.add.at(weights, triangles.ravel(), np.repeat(areas, 3))
    return weights


def add_force(values, positions, weights, surface, pinned, vertex, vector, radius_m):
    """Add a world-space resultant to free surface nodes in the same body."""
    if radius_m > 0:
        distance = np.linalg.norm(positions[surface] - positions[vertex], axis=1)
        patch = surface[(distance <= radius_m) & ~pinned[surface] & (weights[surface] > 0)]
        if len(patch):
            area = weights[patch]
            values[patch] += (area / area.sum())[:, None] * vector
            return
    if not pinned[vertex]:
        values[vertex] += vector
