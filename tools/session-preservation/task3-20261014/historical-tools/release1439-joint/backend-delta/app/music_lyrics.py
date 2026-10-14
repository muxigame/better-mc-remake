"""Bounded durable lyric provenance; never accepts a URL, secret or audio permission."""
import json,re
NONE={'platform':'none','identifier':'','page':1}
def normalize_origin(value):
    if value is None:return dict(NONE)
    if type(value) is not dict or set(value)!={'platform','identifier','page'}:raise ValueError('invalid_lyric_origin')
    kind,identifier,page=value['platform'],value['identifier'],value['page']
    if type(identifier) is not str or type(page) is not int or not 1<=page<=10000:raise ValueError('invalid_lyric_origin')
    if kind=='none':valid=identifier=='' and page==1
    elif kind in ('netease','qq'):valid=bool(re.fullmatch(r'[1-9][0-9]{0,18}',identifier)) and int(identifier)<=9223372036854775807 and page==1
    elif kind=='bilibili':valid=bool(re.fullmatch(r'BV[A-Za-z0-9]{10}',identifier))
    else:valid=False
    if not valid:raise ValueError('invalid_lyric_origin')
    return {'platform':kind,'identifier':identifier,'page':page}
def encode_origin(value):return json.dumps(normalize_origin(value),separators=(',',':'),sort_keys=True)
