import os,sys,json,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ['FORGE_DATA_DIR']=tempfile.mkdtemp(prefix='forge-generic-')
sys.path.insert(0,str(ROOT/'build'))
import studio_api
for command in ['管理列表','生成A1列表模板','生成设备巡检列表']:
    status,value=studio_api.forge_from_command({'command':command,'theme':'default'})
    print(json.dumps({'command':command,'status':status,'summary':value.get('summary'),'steps':[{'step':s['step'],'ok':s['ok'],'log':s.get('log','') if not s['ok'] else ''} for s in value.get('steps',[])]},ensure_ascii=False))
    assert status==200 and value.get('ok'),value
