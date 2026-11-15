"""Real API contract: import, scope material edits, rebuild and advance."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

@unittest.skipUnless(os.environ.get('IPC_GPU_TEST')=='1','Requires the validated CUDA package')
class UsdInterfaceTests(unittest.TestCase):
    def test_usd_materials_survive_upload_edit_save_and_reconfigure(self):
        from fastapi.testclient import TestClient
        from app import interface
        from app.server import app
        from test_usd_materials import make_asset
        with tempfile.TemporaryDirectory(dir=interface.ROOT/'runtime') as temporary:
            folder=Path(temporary);source=folder/'materials.usda';make_asset(source)
            project=folder/'scene.json'
            with patch.object(interface,'PROJECT',project),TestClient(app) as client:
                result=client.put('/api/asset?name=materials.usda',content=source.read_bytes())
                self.assertEqual(result.status_code,200,result.text)
                info=client.get('/api/project').json()
                self.assertEqual(info['bodies']['/volume']['material']['volume_yield_strain_percent'],3.5)
                self.assertEqual(info['bodies']['/shell']['material']['bending_plastic_hardening'],.4)
                config=info['config']
                config['bodies']={'/volume':{'material':{'youngs_modulus_pa':950000}},
                                  '/shell':{'material':{'bending_rigidity_nm':.0003}}}
                config['contact_mode']='none'
                saved=client.put('/api/project',json=config)
                self.assertEqual(saved.status_code,200,saved.text)
                self.assertNotIn('material_regions',saved.json()['config']['bodies']['/volume'])
                info=client.get('/api/project').json()
                self.assertEqual(info['bodies']['/volume']['material']['youngs_modulus_pa'],950000)
                self.assertEqual(info['bodies']['/volume']['element_material_ids'],[0,1])
                self.assertEqual(info['bodies']['/shell']['element_material_ids'],[0,1])
                with client.websocket_connect('/ws') as ws:
                    ws.send_json({'type':'configure','request_id':1,'config':info['config']})
                    state=ws.receive_json()
                    self.assertEqual(state['type'],'state',state)
                    self.assertEqual(state['attachments'][0]['total_stiffness_n_m'],4200)
                    self.assertEqual(state['bodies']['/volume']['element_material_ids'],[0,1])
                    ws.send_json({'type':'step','request_id':2})
                    self.assertEqual(ws.receive_json()['frame'],1)
                config['bodies']['/volume']['material_regions']=[]
                self.assertEqual(client.put('/api/project',json=config).status_code,200)
                self.assertEqual(client.get('/api/project').json()['bodies']['/volume']['element_material_ids'],[0,0])

    def test_upload_select_material_reset_and_legacy_messages(self):
        from fastapi.testclient import TestClient
        from app import interface
        from app.server import app
        root=interface.ROOT
        with tempfile.TemporaryDirectory(dir=root/'runtime') as temporary:
            project=Path(temporary)/'scene.json'
            project.write_text((root/'scene.json').read_text(encoding='utf-8-sig'),encoding='utf-8')
            with patch.object(interface,'PROJECT',project), TestClient(app) as client:
                upload=client.put('/api/asset?name=PaperBag.usd',content=(root/'examples/paperbag/PaperBag.usd').read_bytes())
                self.assertEqual(upload.status_code,200,upload.text)
                result=client.get('/api/project')
                self.assertEqual(result.status_code,200,result.text)
                info=result.json()
                self.assertEqual(len(info['bodies']),3)
                config=info['config']
                config['dt']=.002
                config['contact_mode']='none'
                left,right='/root/Rope001','/root/Rope002'
                E=info['bodies'][left]['material']['youngs_modulus_pa']
                config['bodies']={left:{'material':{'youngs_modulus_pa':2*E},
                    'material_regions':[{'elements':[0],'material':{'density_kg_m3':750.}}]},
                    '/root/PaperBag':{'mass_kg':.12,'material':{'self_friction':.25}}}
                saved=client.put('/api/project',json=config)
                self.assertEqual(saved.status_code,200,saved.text)
                reread=client.get('/api/project').json()
                self.assertEqual(reread['bodies'][left]['material']['youngs_modulus_pa'],2*E)
                self.assertEqual(reread['bodies'][right]['material']['youngs_modulus_pa'],E)
                self.assertEqual(reread['bodies']['/root/PaperBag']['mass_kg'],.12)
                self.assertEqual(reread['bodies'][left]['element_material_ids'][0],1)
                with client.websocket_connect('/ws') as ws:
                    ws.send_json({'type':'configure','request_id':1,'config':config})
                    state=ws.receive_json()
                    self.assertEqual(state['type'],'state',state)
                    self.assertEqual(state['request_id'],1)
                    self.assertEqual(len(state['bodies']),3)
                    self.assertEqual(len(state['attachments']),2)
                    self.assertEqual(state['frame'],0)
                    self.assertEqual(state['config']['dt'],.002)
                    self.assertEqual(state['config']['contact_mode'],'none')
                    self.assertEqual(state['bodies']['/root/PaperBag']['ground_friction'],.5)
                    ws.send_json({'type':'step','request_id':2})
                    step=ws.receive_json()
                    self.assertEqual(step['frame'],1,step)
                    self.assertEqual(step['time'],.002)
                    ws.send_json({'type':'snapshot','request_id':3})
                    self.assertIn('faces',ws.receive_json())
                before=project.read_bytes()
                config['bodies']={'/missing':{'material':{'density_kg_m3':1}}}
                self.assertEqual(client.put('/api/project',json=config).status_code,400)
                self.assertEqual(project.read_bytes(),before)
                archive=io.BytesIO()
                with zipfile.ZipFile(archive,'w') as z:z.writestr('../outside.usda','#usda 1.0')
                self.assertEqual(client.put('/api/asset?name=bad.zip',content=archive.getvalue()).status_code,400)
                self.assertEqual(project.read_bytes(),before)

if __name__=='__main__':unittest.main()
