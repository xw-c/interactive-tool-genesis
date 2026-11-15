import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
from pydantic import ValidationError

from app.config import Force, Setup
from app.forces import add_force, surface_vertex_areas


class ForcePatchTests(unittest.TestCase):
    def test_radius_round_trip_and_legacy_input(self):
        legacy = Force(vertex=0, vector=(0, 0, -1))
        self.assertEqual(legacy.radius_m, 0)
        setup = Setup(forces=[Force(vertex=2, vector=(2, -1, 3), radius_m=.008)])
        self.assertEqual(Setup.model_validate_json(setup.model_dump_json()), setup)
        with self.assertRaises(ValidationError):
            Force(vertex=0, vector=(0, 0, -1), radius_m=-.001)

    def test_area_weighting_and_pins_preserve_resultant(self):
        positions = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [-2., 0., 0.],
                              [0., 0., .1]])
        triangles = np.array([[0, 1, 2], [0, 2, 3]])
        weights = surface_vertex_areas(positions, triangles)
        surface = np.array([0, 1, 2, 3])
        uneven = np.zeros_like(positions)
        free = np.zeros(len(positions), dtype=bool)
        vector = np.array([3., -6., 9.])
        add_force(uneven, positions, weights, surface, free, 0, vector, 3.)
        np.testing.assert_allclose(uneven[:4], np.array([3, 1, 3, 2])[:, None] * vector / 9)
        pinned = np.array([False, True, False, False, False])
        values = np.zeros_like(positions)
        add_force(values, positions, weights, surface, pinned, 0, vector, 1.)
        np.testing.assert_allclose(values[0], vector/2)
        np.testing.assert_allclose(values[2], vector/2)
        np.testing.assert_allclose(values[[1, 3, 4]], 0)
        np.testing.assert_allclose(values.sum(axis=0), vector)
        add_force(values, positions, weights, surface, pinned, 3, vector, 0.)
        np.testing.assert_allclose(values[3], vector)

    @unittest.skipUnless(os.environ.get('IPC_GPU_TEST') == '1', 'Requires the CUDA shell backend')
    def test_shell_force_radius_reaches_native_nodes(self):
        from app.engine import GenesisEngine
        import uipc

        with tempfile.TemporaryDirectory() as folder:
            mesh = Path(folder) / 'shell.obj'
            mesh.write_text('v 0 0 .3\nv .01 0 .3\nv .01 .01 .3\nv 0 .01 .3\nf 1 2 3\nf 1 3 4\n',
                            encoding='utf-8')
            engine = GenesisEngine(Setup(mesh=str(mesh), body_type='shell', contact_mode='none',
                                         dt=.001, gravity=(0, 0, 0)))
            try:
                vector = np.array([0., 0., .001])
                for radius, expected_count in ((0., 1), (.015, 4)):
                    with self.subTest(radius_m=radius):
                        engine.set_forces([Force(vertex=0, vector=vector, radius_m=radius)])
                        engine.step(snapshot=False)
                        native = uipc.view(engine._ipc_geometry.vertices().find('external_force')).reshape(-1, 3)
                        ids = np.flatnonzero(np.any(native != 0, axis=1))
                        self.assertEqual(len(ids), expected_count)
                        np.testing.assert_allclose(native.sum(axis=0), vector)
                        if radius == 0:
                            np.testing.assert_array_equal(ids, engine.surface_ids[:1])
            finally:
                engine.close()
