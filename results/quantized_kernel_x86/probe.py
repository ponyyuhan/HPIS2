import ctypes as C, numpy as np,h5py,os,sys,time
from threadpoolctl import threadpool_limits
from hpis.codec import unit_vectors,rotation,VectorCodec,encrypt_item,AESGCM
from integration.mapped_index import native
from pathlib import Path
P=Path(os.environ.get('COARSE_DIR','/tmp/hpis-coarse-kernel'))
lib=C.CDLL(str(P/'fused.so'));p=C.c_void_p
lib.coarse_prepare.argtypes=[p,C.c_int,C.c_int];lib.coarse_prepare.restype=p
lib.coarse_free.argtypes=[p];os.environ['HPIS_MAPPED_NATIVE']=os.environ.get('BASELINE_LIB','/tmp/hpis-exact-arch/rank.dylib')
old=native.load_library(); new=lib.hpis_rank_q8; new.argtypes=old.argtypes+[p,p];new.restype=C.c_int
import json,gzip
from bench.run import oracle
with h5py.File(os.environ['GLOVE']) as f, threadpool_limits(1):
 gold=oracle(f['train'][:20000], f['test'][:256], 10)
raw=open(os.environ.get('HPIS_Q8_SAMPLES','q8-raw.jsonl'),'w')
threadpool_limits(1);N=20000;D=100;k=10
with h5py.File(os.environ.get('GLOVE','/Users/yuhan/Library/Caches/hpis-search/glove-100-angular-first100k.hdf5')) as f:
 x=unit_vectors(f['train'][:N],D);qs=unit_vectors(f['test'][:256],D)
rot=rotation(D);codec=VectorCodec(D,.25,rot);y=codec.sketch(x); er=qs@rot
key=AESGCM.generate_key(256);cipher=AESGCM(key); body=b''.join(encrypt_item(cipher,b'vector',i,row.tobytes()) for i,row in enumerate(x)); bptr=C.cast(C.c_char_p(body),p)
ids=np.arange(N,dtype=np.int64);out=np.empty(k,dtype=np.int64);opened=(C.c_int64*2)()
st=np.empty(N,dtype='f4')
rng=np.random.default_rng(8)
for mode,M,Q in [('plain',x,qs),('ppe',y,er)]:
 prep=lib.coarse_prepare(M.ctypes.data,N,D); secure=mode=='ppe';margin=8*D*np.finfo('f4').eps if not secure else 8*(D*np.sqrt(D)+4)*np.finfo('f4').eps;r=margin+.25/4 if secure else margin
 lens=np.sqrt(np.einsum('ij,ij->i',M,M,dtype='f8'));chord=np.sqrt(np.maximum(0,r*r-(lens-1)**2)/lens);angles=2*np.arcsin(chord/2)
 inv=1/lens; sn=np.sin(angles);cs=np.cos(angles)
 times=[]; counts=[]; exact=0
 for idx in list(range(16))+list(range(256))*2:
  q=qs[idx];rotq=Q[idx]
  order=rng.permutation(2); dt=np.zeros(2);res=[None,None];cnt=[0,0]
  for j in order:
   tic=time.perf_counter_ns()
   if j==0: np.matmul(M,rotq,out=st)
   fn=old if not j else new
   ext=[] if j==0 else [prep,rotq.ctypes.data]
   code=fn(N,D,k,st.ctypes.data,ids.ctypes.data,M.ctypes.data,bptr if secure else None,key if secure else None,q.ctypes.data,inv.ctypes.data,sn.ctypes.data,cs.ctypes.data,r,margin,out.ctypes.data,C.addressof(opened),*ext)
   dt[j]=(time.perf_counter_ns()-tic)/1000
   assert code==0;res[j]=out.copy();cnt[j]=opened[0]
  if not np.array_equal(*res) or res[0].tolist()!=gold[idx]:
   raise AssertionError((idx,res,gold[idx]))
  if len(times)>=16:
   raw.write(json.dumps(dict(mode=mode, query=idx, pair=len(times)-16, order=order.tolist(), us=dt.tolist(), ids=res[1].tolist(), opened=cnt))+'\n')
  if idx<16 or len(times)>0: pass
  times.append(dt);counts.append(cnt)
 t=np.array(times[16:]);print(mode,'baseline/q8 fused us mean',np.mean(t,axis=0),'median',np.median(t,axis=0),'P95',np.percentile(t,95,axis=0),'open',np.mean(counts,axis=0),'correct',len(t),flush=True)
 lib.coarse_free(prep)

raw.close()
