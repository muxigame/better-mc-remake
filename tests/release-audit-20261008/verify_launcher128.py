import pathlib,json,hashlib,base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
O=pathlib.Path(__file__).resolve().parent
A=O/'launcher128-artifacts'
m=json.loads((A/'launcher-release.json').read_text(encoding='utf-8-sig'))
c=json.loads(pathlib.Path('better-mc-remake/client/tauri/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
data=(A/'BMC [Remake] setup.exe').read_bytes()
assert m['version']=='1.2.8'
assert hashlib.sha256(data).hexdigest()==m['sha256']
raw=(A/'BMC [Remake] setup.exe.sig').read_text(encoding='utf-8-sig').strip()
assert raw==m['signature']
pub=base64.b64decode(base64.b64decode(c['plugins']['updater']['pubkey']).decode().splitlines()[1])
lines=base64.b64decode(raw).decode().splitlines();sig=base64.b64decode(lines[1])
assert sig[2:10]==pub[2:10]
key=Ed25519PublicKey.from_public_bytes(pub[10:])
def message(b):return hashlib.blake2b(b).digest() if sig[:2]==b'ED' else b
key.verify(sig[10:],message(data))
key.verify(base64.b64decode(lines[3]),sig[10:]+lines[2][len('trusted comment: '):].encode())
try:key.verify(sig[10:],message(data[:-1]+bytes([data[-1]^1])))
except InvalidSignature:tamper=True
else:raise AssertionError('Tampered installer accepted')
r={'version':m['version'],'sha256':m['sha256'],'bytes':len(data),'signatureMetadataMatches':True,'updaterSignatureVerified':True,'trustedCommentVerified':True,'tamperedInstallerRejected':tamper,'projectPublicKeyUsed':True,'privateKeyRead':False,'productionPublished':False}
(O/'launcher128-signature-verification.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
print(json.dumps(r))
