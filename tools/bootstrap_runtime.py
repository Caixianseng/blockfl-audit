"""Download official portable Besu/JDK into this project, verifying checksums."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import urllib.request
import zipfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / 'tools/downloads'
RUNTIME = ROOT / 'tools/runtime'

def read(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'BlockFL-course-demo/1.0'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()

def download(url, expected, name, mirror=False):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS / name
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        temp = target.with_suffix('.part')
        transport_url = 'https://ghfast.top/' + url if mirror else url
        req = urllib.request.Request(transport_url, headers={'User-Agent': 'BlockFL-course-demo/1.0'})
        with urllib.request.urlopen(req, timeout=120) as response, temp.open('wb') as out:
            total = 0
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
                total += len(chunk)
                if total % (10 * 1024 * 1024) == 0: print(f'{name}: {total//1048576} MiB', flush=True)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Checksum mismatch: {name}')
        temp.replace(target)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target) as z:
        for entry in z.infolist():
            if not (RUNTIME / entry.filename).resolve().is_relative_to(RUNTIME.resolve()):
                raise RuntimeError('Unsafe archive path')
        z.extractall(RUNTIME)
    print(f'Verified and extracted {name}', flush=True)
    return {'url': url, 'sha256': expected, 'archive': name, 'transport': 'ghfast.top mirror, pinned official checksum' if mirror else 'official'}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mirror', action='store_true', help='Use public transport mirror; verify pinned official SHA-256')
    args = parser.parse_args()
    lock=json.loads((ROOT/'tools/runtime.lock.json').read_text(encoding='utf-8'))
    besu_version=lock['besu_version']
    # Independently obtained official release digests, never trust mirror digests.
    jobs=[(d['url'],d['sha256'],d['archive']) for d in lock['downloads']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        provenance = list(executor.map(lambda job: download(*job, mirror=args.mirror), jobs))
    java = RUNTIME/lock['java_directory']/'bin/java.exe'
    if not java.exists():raise RuntimeError('Pinned JDK executable missing after extraction')
    besu = RUNTIME / f'besu-{besu_version}'
    config = {'java_home': str(java.parents[1]), 'besu_home': str(besu), 'besu_version': besu_version, 'downloads': provenance}
    (ROOT / 'network/runtime.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    print(json.dumps(config, indent=2), flush=True)

if __name__ == '__main__':
    main()
