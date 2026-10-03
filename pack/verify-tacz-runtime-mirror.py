"""Compare actual resource bytes after TaCZ rewrites its ZIP container/name."""
import argparse, hashlib, json, zipfile
from pathlib import Path, PurePosixPath

def contents(path):
    result={};total=0
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            if item.is_dir():continue
            parts=PurePosixPath(item.filename)
            if parts.is_absolute() or '..' in parts.parts or '\\' in item.filename or item.filename in result:
                raise ValueError('Invalid or duplicate ZIP entry')
            total+=item.file_size
            if item.file_size>16*1024*1024 or total>64*1024*1024 or len(result)>=4096:
                raise ValueError('ZIP exceeds mirror verification limits')
            result[item.filename]=hashlib.sha256(archive.read(item)).digest()
    if not result:raise ValueError('Empty resource pack')
    return result

def verify(server,client):
    live=contents(server);published=contents(client)
    if live!=published:raise ValueError('TaCZ resource content differs between server and client')
    return len(live)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--server',required=True,type=Path);parser.add_argument('--client',required=True,type=Path)
    args=parser.parse_args()
    count=verify(args.server,args.client)
    print(json.dumps({'taczResourceEntriesVerified':count,'allResourceBytesIdentical':True}))
