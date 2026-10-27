import pathlib, hashlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
CAP=pathlib.Path('/tmp/opencode/captures')
DAT=ROOT / 'vendor' / 'Magician-NES' / 'DAT'

# load all DAT chr hashes
dat_chrs={}
for f in DAT.glob('*.CHR'):
    if f.stat().st_size==4096:
        dat_chrs[hashlib.sha1(f.read_bytes()).hexdigest()] = f.name

for capdir in sorted(CAP.iterdir()):
    if not capdir.is_dir(): continue
    full=(capdir/'chrfull.bin').read_bytes()
    banks=[full[i:i+4096] for i in range(0,len(full),4096)]
    print(f"\n=== {capdir.name} ===")
    # nt0
    nt=(capdir/'nt0.bin').read_bytes()[:960] if (capdir/'nt0.bin').exists() else None
    if nt:
        print(f"  nt0 distinct tiles: {len(set(nt))}  (min {min(nt)} max {max(nt)})")
    unmatched=[]
    for i,b in enumerate(banks):
        h=hashlib.sha1(b).hexdigest()
        nz=sum(1 for x in b if x!=0)
        if nz<50:
            name='(empty)'
        else:
            name=dat_chrs.get(h, '*** MISSING/UNKNOWN ***')
        if name.startswith('***'):
            unmatched.append((i,name,h))
        print(f"  bank {i:02x}: nz={nz:5d} -> {name}")
    if unmatched:
        print(f"  UNMATCHED banks: {len(unmatched)}")
        for i,name,h in unmatched:
            print(f"     bank {i:02x} sha1={h}")
