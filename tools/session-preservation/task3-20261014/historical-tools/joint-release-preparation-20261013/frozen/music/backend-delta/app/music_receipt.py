"""Purpose-bound short-lived burn proof signed with the existing OIDC signer."""
import time
import jwt


class BurnReceipts:
    AUDIENCE = 'muxi-music-burn'
    PURPOSE = 'music-burn-v1'

    def __init__(self, signer, issuer, clock=time.time):
        self.signer, self.issuer, self.clock = signer, issuer.rstrip('/'), clock

    def sign(self, row, receipt):
        from .music_upload import MusicError
        now = int(self.clock())
        if not row['burn_nonce'] or row['expires'] <= now:
            raise MusicError(409, 'burn_context_required')
        claims = {'iss': self.issuer, 'aud': self.AUDIENCE, 'purpose': self.PURPOSE,
            'sub': str(row['uid']), 'uid': row['uid'], 'gameSession': row['session'],
            'burnNonce': row['burn_nonce'], 'iat': now, 'exp': min(now+120, int(row['expires'])),
            **{k: receipt[k] for k in ('jobId','receiptId','url','title','sha256','bytes','duration','license','credit')}}
        proof = self.signer.id_token(claims)
        if len(proof) > 8192:
            raise MusicError(400, 'invalid_metadata_size')
        return proof

    def verify(self, proof):
        from .music_upload import MusicError
        try:
            if not isinstance(proof, str) or not 1 <= len(proof) <= 8192: raise ValueError()
            header = jwt.get_unverified_header(proof)
            if header.get('kid') != self.signer.kid or header.get('alg') != 'RS256': raise ValueError()
            claims = jwt.decode(proof, jwt.PyJWK.from_dict(self.signer.jwk).key, algorithms=['RS256'],
                audience=self.AUDIENCE, issuer=self.issuer,
                options={'require':['exp','iat','iss','aud','sub','purpose','uid','gameSession','burnNonce','jobId','receiptId']})
            if claims['purpose'] != self.PURPOSE or claims['sub'] != str(claims['uid']): raise ValueError()
            if type(claims['uid']) is not int or claims['exp']-claims['iat'] > 120: raise ValueError()
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise MusicError(401, 'invalid_burn_proof') from None
