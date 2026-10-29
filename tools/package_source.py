"""Package code, report and audit evidence; exclude runtime, datasets and keys."""
import argparse
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED_PARTS={'.venv','node_modules','__pycache__','.pytest_cache','.pytest-tmp','.test-python','.pip-cache','.npm-cache','.git'}

def included(path):
    rel=path.relative_to(ROOT)
    if any(x in EXCLUDED_PARTS for x in rel.parts):return False
    if any(x.startswith('.pytest-') for x in rel.parts):return False
    if rel.parts[0]=='data':return False
    if len(rel.parts)>1 and rel.parts[0]=='tools' and rel.parts[1] in {'downloads','runtime'}:return False
    if len(rel.parts)>1 and rel.parts[0]=='network' and rel.parts[1] in {'nodes','accounts.json','pids.json','runtime.json','genesis.json','validators.json'}:return False
    if rel.parts[0]=='results' and len(rel.parts)>1 and rel.parts[1].startswith('pytest-'):return False
    if path.suffix in {'.pyc','.part'}:return False
    if path.name in {'.experiment.lock'}:return False
    if path.name.startswith(('.job','.dashboard-launch')):return False
    return True

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT.parent/'blockfl-audit-source.zip');args=p.parse_args()
    output=args.output.resolve()
    if output.is_relative_to(ROOT):raise ValueError('Archive must be outside the source directory')
    files=[f for f in ROOT.rglob('*') if f.is_file() and included(f)]
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for f in files:z.write(f,Path('blockfl-audit')/f.relative_to(ROOT))
    print(json.dumps({'archive':str(output),'files':len(files),'bytes':output.stat().st_size},indent=2))

if __name__=='__main__':main()
