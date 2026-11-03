"""Run either Temp Pivot engine on the existing disposable production snapshot."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('pivot_parity_test_helpers', ROOT/'tests/test_temp_pivot_animo.py')
test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test)
cmds, pivot, tf, kernel = test.cmds, test.pivot, test.tf, test.kernel


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--engine',choices=('original','animkey'),required=True)
    args=parser.parse_args()
    out=ROOT/'dist/temp-pivot-animo'
    snapshot=ROOT/'dist/scene-parity/prepared_snapshot.mb'
    if not snapshot.exists():raise RuntimeError('Prepare the local scene snapshot before running this optional comparison')
    original=test.original_module() if args.engine=='original' else None
    result={'engine':args.engine,'source':'existing untouched animation snapshot','cases':{}}
    cmds.file(prompt=False)
    cmds.scriptEditorInfo(suppressWarnings=True,suppressInfo=True)
    with tempfile.TemporaryDirectory() as storage, mock.patch.object(test.configMod,'get_user_folder_path',return_value=storage):
        for actor in ('King_Rigging','Goblin_Rigging'):
            for kind,names in [('ik_root',['IKLeg_L','IKLeg_R','PoleLeg_L','PoleLeg_R','RootX_M']),
                               ('fk',['FKWrist_L','FKShoulder_L','FKChest_M'])]:
                name=actor+'_'+kind
                print('Loading baseline for '+args.engine+' '+name,flush=True)
                pivot.deactivate_temp_pivot(restore_selection=False)
                try:
                    cmds.file(str(snapshot),open=True,force=True,prompt=False,ignoreVersion=True,
                              executeScriptNodes=False,loadReferenceDepth='all')
                except RuntimeError:
                    pass  # Known missing render/scene references; controls verified below.
                cmds.evaluationManager(mode='off')
                cmds.autoKeyframe(state=False)
                cmds.currentTime(257)
                nodes=tf.transforms([actor+':'+n for n in names])
                if len(nodes)!=len(names):raise RuntimeError('Required control unavailable: '+name)
                harness=test.TempPivotAnimoTests()
                harness.temp=type('Storage',(),{'name':storage})()
                if original:
                    controller=harness.setup_original(original,nodes);helper=original.PIVOT_NULL
                else:
                    pivot.activate_temp_pivot(nodes,edit_pivot=False)
                    controller=pivot._session;helper=controller.proxy.name()
                    pivot.begin_gesture()
                for attr,delta in [('rz',6.0),('tx',3.0),('ty',-2.0)]:
                    cmds.setAttr(helper+'.'+attr,cmds.getAttr(helper+'.'+attr)+delta)
                    if original:controller.on_timer_tick()
                cmds.setKeyframe(helper,attribute=kernel.TR_ATTRS)
                if original:
                    controller.on_timer_tick();controller.clear()
                else:
                    controller.tick(force=True);controller.finish();pivot.deactivate_temp_pivot()
                entry={'keys':harness.curves(nodes),'matrices':{}}
                for frame in (256,257,258):
                    cmds.currentTime(frame)
                    entry['matrices'][str(frame)]=harness.snapshot(nodes)
                result['cases'][name]=entry
                (out/(args.engine+'-scene.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
                print('Completed '+name,flush=True)
    cmds.file(new=True,force=True)
    print('Completed all scene comparisons for '+args.engine,flush=True)


if __name__=='__main__':main()
