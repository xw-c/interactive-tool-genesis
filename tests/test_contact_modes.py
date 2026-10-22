"""Contact mode validation, UI round trips and self-pair filtering."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from pydantic import ValidationError
from app.config import Setup,Material


class ContactModeTests(unittest.TestCase):
    def test_defaults_validation_and_round_trip(self):
        self.assertEqual(Setup().contact_mode,'ground_only')
        for body in ('shell','volume'):
            for mode in ('full','no_self','ground_only','none'):
                c=Setup(body_type=body,contact_mode=mode)
                self.assertEqual(Setup.model_validate_json(c.model_dump_json()),c)
        for mode in ('off','fem',None,False):
            with self.subTest(mode=mode),self.assertRaises(ValidationError):Setup(contact_mode=mode)

    def test_self_pair_override_runs_after_original_registration(self):
        from app.engine import GenesisEngine
        for body in ('shell','volume'):
            e=GenesisEngine.__new__(GenesisEngine)
            e.config=Setup(body_type=body,contact_mode='no_self');e.entity=object()
            c=SimpleNamespace(_ipc_cloth_contacts={},_ipc_fem_contacts={},
                _ipc_contact_tabular=Mock(),options=SimpleNamespace(contact_resistance=1e9))
            element=object()
            def register():
                contacts=c._ipc_cloth_contacts if body=='shell' else c._ipc_fem_contacts
                contacts[e.entity]=element
                c._ipc_contact_tabular.insert('ground',element,.4,1e9,True)
                c._ipc_contact_tabular.insert(element,element,.4,1e9,True)
            c._register_contact_pairs=register
            e.scene=SimpleNamespace(sim=SimpleNamespace(coupler=c))
            e._disable_self_contact();c._register_contact_pairs()
            self.assertEqual(c._ipc_contact_tabular.insert.call_args_list[-1].args,(element,element,.4,1e9,False))
            self.assertEqual(c._ipc_contact_tabular.insert.call_args_list[0].args,('ground',element,.4,1e9,True))

    def test_disabled_modes_do_not_emit_self_contact_scale_warning(self):
        from app.mesh import contact_check
        for mode in ('none','no_self','ground_only'):
            result=contact_check([],[],Setup(contact_mode=mode))
            self.assertFalse(result['enabled']);self.assertEqual(result['warnings'],[])

    def test_ground_only_disables_all_object_pairs_and_preserves_planes(self):
        from app.engine import GenesisEngine
        e=GenesisEngine.__new__(GenesisEngine)
        elements=[object(),object(),object()];ground=object();pairs={}
        def insert(a,b,mu,k,enabled):pairs[frozenset((a,b))]=(mu,k,enabled)
        c=SimpleNamespace(_ipc_cloth_contacts={1:elements[0]},_ipc_fem_contacts={2:elements[1]},
            _ipc_abd_contacts={3:elements[2]},_ipc_contact_tabular=SimpleNamespace(insert=insert),
            options=SimpleNamespace(contact_resistance=1e9))
        def register():
            for i,a in enumerate(elements):
                for b in elements[i:]:insert(a,b,.3,1e9,True)
                insert(ground,a,.4,2e9,True)
        c._register_contact_pairs=register;e.scene=SimpleNamespace(sim=SimpleNamespace(coupler=c))
        e._ground_only_contacts();c._register_contact_pairs()
        for i,a in enumerate(elements):
            self.assertEqual(pairs[frozenset((ground,a))],(.4,2e9,True))
            for b in elements[i:]:self.assertFalse(pairs[frozenset((a,b))][2])

    @unittest.skipUnless(shutil.which('node'),'Requires Node.js')
    def test_contact_form_round_trip(self):
        spec=dict(material_schema=Material.model_json_schema(),config=Setup().model_dump(mode='json'),
            models=dict(shell='BW',volume='SNH',bending_model=[dict(value='DiscreteShellBending',label='DSB')]),
            mesh=dict(file='model.obj',vertices=4,triangles=2),genesis_version='1.4.1')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'schema.json';path.write_text(json.dumps(spec),encoding='utf-8')
            result=subprocess.run([shutil.which('node'),'tests/test_contact_form.cjs',str(path)],
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(os.environ.get('IPC_CONTACT_GPU_TEST')=='1','Set IPC_CONTACT_GPU_TEST=1 for live session switching')
    def test_live_session_switch_preserves_inputs(self):
        from app.interface import Simulation
        config=Setup(mesh='benchmarks/shell_1k.obj',gravity=(0,0,0),pins=[0,1],
            forces=[dict(vertex=2,vector=[0,0,.001])])
        simulation=Simulation()
        try:
            for mode in ('full','no_self','ground_only','none','full'):
                config.contact_mode=mode
                state=simulation.configure(config.model_dump(mode='json'))
                self.assertEqual(state['config']['contact_mode'],mode)
                self.assertEqual(state['time'],0)
                self.assertEqual(state['config']['forces'],config.model_dump(mode='json')['forces'])
                self.assertEqual(simulation.engine.step()['frame'],1)
            state=simulation.set_pins([0,1,3])
            self.assertEqual(state['config']['contact_mode'],'full')
            self.assertEqual(state['time'],0)
        finally:simulation.close()


if __name__=='__main__':unittest.main()
