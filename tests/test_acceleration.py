"""Process isolation and the frontend-to-Genesis acceleration contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('IPC_CONTACT_GPU_TEST')=='1','Requires both precision packages and an idle CUDA GPU')
class AccelerationTests(unittest.TestCase):
    def test_native_options_match_selected_package(self):
        from run import runtime_environment
        code = """
import os
from app.config import Setup
from app.engine import GenesisEngine
import genesis as gs
import uipc
enabled=os.environ['INTERACTIVE_IPC_ACCELERATION_ENABLED']=='1'
engine=GenesisEngine(Setup(is_acceleration_enabled=enabled,mesh='benchmarks/shell_1k.obj',contact_mode='none'))
try:
    assert uipc.build_info()['float_bytes']==(4 if enabled else 8)
    assert gs.np_float().itemsize==(4 if enabled else 8)
    native=engine.scene.sim.coupler._ipc_scene.config()
    for key in ('fem/bending/pseudo','contact/friction/pseudo'):
        assert int(uipc.view(native.find(key))[0])==int(enabled),key
    engine.step(snapshot=False)
finally:engine.close();gs.destroy()
"""
        for enabled in (False,True):
            with self.subTest(enabled=enabled):
                subprocess.run([sys.executable,'-c',code],env=runtime_environment(enabled),check=True,timeout=180)

    def test_websocket_switch_and_saved_configuration(self):
        from fastapi.testclient import TestClient
        import psutil
        from app.config import Setup
        native_before={name for name in ('genesis','uipc') if name in sys.modules}
        from app.server import app, ROOT
        self.assertEqual({name for name in ('genesis','uipc') if name in sys.modules},native_before)
        config=Setup(mesh='benchmarks/shell_1k.obj',contact_mode='none',dt=.005).model_dump(mode='json')
        parent=psutil.Process()
        original={p.pid for p in parent.children()}
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as directory:
            project=Path(directory)/'scene.json';project.write_text(json.dumps(config))
            with patch.dict(os.environ,{'INTERACTIVE_IPC_PROJECT':str(project)}), patch('app.config.PROJECT',project), TestClient(app) as client:
                self.assertIn('id="is_acceleration_enabled"',client.get('/').text)
                info=client.get('/api/project');self.assertEqual(info.status_code,200,info.text)
                spec=Path(directory)/'spec.json';spec.write_text(json.dumps(info.json()))
                subprocess.run(['node','tests/test_contact_form.cjs',str(spec)],check=True,cwd=ROOT)
                with client.websocket_connect('/ws') as ws:
                    previous=None
                    for request_id,enabled in enumerate((False,True,False),1):
                        config['is_acceleration_enabled']=enabled
                        ws.send_json({'type':'configure','request_id':request_id,'config':config})
                        state=ws.receive_json()
                        self.assertEqual(state['type'],'state',state)
                        self.assertEqual(state['request_id'],request_id)
                        self.assertEqual(state['precision_bits'],32 if enabled else 64)
                        self.assertEqual(state['config']['is_acceleration_enabled'],enabled)
                        self.assertEqual(state['frame'],0)
                        workers={p.pid for p in parent.children()}-original
                        self.assertEqual(len(workers),1,workers)
                        if previous is not None:self.assertNotIn(previous,workers)
                        previous=next(iter(workers))
                        ws.send_json({'type':'step','request_id':10+request_id})
                        stepped=ws.receive_json()
                        self.assertEqual(stepped['type'],'state',stepped)
                        self.assertEqual(stepped['frame'],1)
                        if enabled:
                            saved=client.put('/api/project',json=config)
                            self.assertEqual(saved.status_code,200,saved.text)
                            self.assertTrue(json.loads(project.read_text())['is_acceleration_enabled'])
                            self.assertTrue(client.get('/api/project').json()['config']['is_acceleration_enabled'])
                    invalid=dict(config,is_acceleration_enabled='false')
                    ws.send_json({'type':'configure','config':invalid})
                    self.assertEqual(ws.receive_json()['type'],'error')
                    ws.send_json({'type':'snapshot'})
                    self.assertEqual(ws.receive_json()['precision_bits'],64)
                    with patch.dict(os.environ,{'INTERACTIVE_IPC_FP32_PACKAGE':os.environ['INTERACTIVE_IPC_FP64_PACKAGE']}):
                        ws.send_json({'type':'configure','config':dict(config,is_acceleration_enabled=True)})
                        failed=ws.receive_json()
                        self.assertEqual(failed['type'],'error',failed)
                        self.assertIn('requires FP32',failed['message'])
                        self.assertEqual({p.pid for p in parent.children()}-original,set())
                    ws.send_json({'type':'configure','config':config})
                    self.assertEqual(ws.receive_json()['precision_bits'],64)
                    ws.send_json({'type':'forces','forces':[]})
                    self.assertEqual(ws.receive_json()['type'],'state')
                    ws.send_json({'type':'pins','pins':[]})
                    self.assertEqual(ws.receive_json()['frame'],0)
                self.assertEqual({name for name in ('genesis','uipc') if name in sys.modules},native_before)
            self.assertEqual({p.pid for p in parent.children()}-original,set())

        with TestClient(app) as client:
            self.assertEqual(client.get('/').status_code,200)


if __name__=='__main__':unittest.main()
