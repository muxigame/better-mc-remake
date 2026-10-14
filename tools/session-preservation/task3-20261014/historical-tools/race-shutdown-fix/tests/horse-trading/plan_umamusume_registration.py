"""Read-only minimal patch plan for a release-owner-verified six-SKU YSM ID map."""
from pathlib import Path
import argparse, json

REPO=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registered-ids',type=Path,required=True)
    args=parser.parse_args()
    observed=json.loads(args.registered_ids.read_text(encoding='utf-8'))
    policy=json.loads((REPO/'src/main/resources/umamusume_catalog_v1.json').read_text(encoding='utf-8'))
    known={p['sku']:p for p in policy['catalog']}
    if not isinstance(observed,dict) or set(observed)!=set(known):parser.error('Exactly the six approved SKU keys are required')
    changes=[];prefixes=set()
    for sku,product in known.items():
        actual=observed[sku]
        if not isinstance(actual,str) or actual.count('/')!=1 or any(ord(c)<32 for c in actual):parser.error('Invalid registered ID for '+sku)
        prefix,leaf=actual.split('/')
        if not prefix or not all(c.isalnum() or c=='_' for c in prefix):parser.error('Unexpected namespace; inspect the actual registration evidence')
        if leaf!=product['modelId'].split('/')[-1]:parser.error('Leaf changed for '+sku+'; separate verified file directory from registration key before patching')
        prefixes.add(prefix)
        if actual!=product['modelId']:changes.append(dict(sku=sku,before=product['modelId'],after=actual))
    if len(set(observed.values()))!=6:parser.error('Registered IDs must be unique')
    print(json.dumps(dict(readOnly=True,registrationVerifiedByThisTool=False,inputMustComeFromReleaseOwner=True,
        modelIdChanges=changes,registeredNamespaces=sorted(prefixes),
        catalogs=['src/main/resources/umamusume_catalog_v1.json','../better-mc-remake/server/app/umamusume_catalog_v1.json'],
        validatorsToReview=['UmamusumeCatalog.read','horse_trading.valid_maid_catalog'] if changes else [],
        pricesAffixesSkusUnchanged=True,enableFlagsChanged=False,clientRenderStillRequired=True),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
