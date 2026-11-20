/* Exact ranking over an immutable sealed vector index. One EVP context per
 * request; every selected vector is authenticated before reading its values. */
#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#include <float.h>
#include <string.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#ifdef HPIS_MAPPED_Q8
#include "q8/q8.h"
#include "q8/fine_dot.h"
#endif

typedef struct {
    int64_t pos, id;
    double distance;
} Hit;

static int worse(Hit a, Hit b) {
    return a.distance > b.distance || (a.distance == b.distance && a.id > b.id);
}

static void swap(Hit *a, Hit *b) {
    Hit tmp = *a; *a = *b; *b = tmp;
}

static void insert(Hit *heap, int *count, int k, Hit value, double *lost) {
    if (*count == k) {
        if (!worse(heap[0], value)) {
            if (lost && value.distance < *lost) *lost=value.distance;
            return;
        }
        if (lost && heap[0].distance < *lost) *lost=heap[0].distance;
        int at = 0;
        heap[0] = value;
        while (2*at+1 < k) {
            int child = 2*at+1;
            if (child+1 < k && worse(heap[child+1],heap[child])) ++child;
            if (!worse(heap[child],heap[at])) break;
            swap(&heap[at],&heap[child]);
            at=child;
        }
    } else {
        int at=(*count)++;
        heap[at]=value;
        while (at && worse(heap[at],heap[(at-1)/2])) {
            int p=(at-1)/2; swap(&heap[p],&heap[at]); at=p;
        }
    }
}

static int ordered(const void *aa,const void *bb) {
    Hit a=*(const Hit *)aa,b=*(const Hit *)bb;
    return worse(a,b)-worse(b,a);
}

typedef struct {
    int dim;
    const float *plain;
    const unsigned char *sealed;
    const int64_t *ids;
    const double *query;
    EVP_CIPHER_CTX *ctx;
    float *scratch;
} Index;

static int distance(Index *idx, int64_t p, double *value) {
    int d=idx->dim;
    const float *row;
    if (!idx->sealed) {
        row=idx->plain+p*d;
    } else {
        size_t width=4*(size_t)d;
        const unsigned char *src=idx->sealed+p*(width+28);
        unsigned char aad[14]={'v','e','c','t','o','r'};
        uint64_t id=idx->ids[p];
        for (int j=0;j<8;++j) aad[13-j]=(unsigned char)(id>>(8*j));
        int written=0,final=0;
        if (EVP_DecryptInit_ex(idx->ctx,NULL,NULL,NULL,src)!=1 ||
            EVP_DecryptUpdate(idx->ctx,NULL,&written,aad,sizeof(aad))!=1 ||
            EVP_DecryptUpdate(idx->ctx,(unsigned char*)idx->scratch,&written,src+12,(int)width)!=1 ||
            EVP_CIPHER_CTX_ctrl(idx->ctx,EVP_CTRL_GCM_SET_TAG,16,(void*)(src+12+width))!=1 ||
            EVP_DecryptFinal_ex(idx->ctx,(unsigned char*)idx->scratch+written,&final)!=1 ||
            written+final!=(int)width) return 0;
        row=idx->scratch;
    }
    double len=0,sum=0;
    for (int j=0;j<d;++j) len+=(double)row[j]*row[j];
    if (!(len>0) || !isfinite(len)) return 0;
    double inv=1/sqrt(len);
    for (int j=0;j<d;++j) {
        double x=(double)row[j]*inv-idx->query[j];
        sum+=x*x;
    }
    *value=sum;
    return isfinite(sum);
}

/* The exact seed is maintained in a max-heap of squared chord distances.
 * Both plaintext and encrypted profiles share this selector and exact scorer. */
static int rank_impl(int64_t n, int d, int k, float *score, const int64_t *ids,
              const float *plain, const unsigned char *sealed,
              const unsigned char *key, const float *q,
              const double *inverse_length,const double *cap_sin,const double *cap_cos,
              double radius,double slack,int64_t *result,int64_t *opened,void *compact,const float *projected) {
    if (n<1 || d<1 || k<1 || k>n) return -1;
#ifdef HPIS_MAPPED_Q8
    double coarse_error=0;
    if (compact) {
        coarse_error=coarse_approx(compact,projected,score);
        if (!isfinite(coarse_error) || coarse_error<0) return -1;
    }
#endif
    Hit *heap=malloc((size_t)k*sizeof(*heap)),*seeds=malloc((size_t)k*sizeof(*seeds));
    double *unit=malloc((size_t)d*sizeof(*unit));
    /* OpenSSL may write a final cipher block; reserve its documented capacity. */
    float *scratch=malloc(((size_t)d+4)*sizeof(*scratch));
    EVP_CIPHER_CTX *ctx=NULL;
    int status=-1,count=0;
    double lost=INFINITY;
    if (!heap || !seeds || !unit || !scratch) goto done;
    double length=0;
    for (int j=0;j<d;++j) length+=(double)q[j]*q[j];
    if (!(length>0) || !isfinite(length)) goto done;
    length=sqrt(length);
    for (int j=0;j<d;++j) unit[j]=(double)q[j]/length;
    if (sealed) {
        ctx=EVP_CIPHER_CTX_new();
        if (!ctx || EVP_DecryptInit_ex(ctx,EVP_aes_256_gcm(),NULL,key,NULL)!=1) goto done;
    }
    Index index={d,plain,sealed,ids,unit,ctx,scratch};
    for (int64_t i=0;i<n;++i) {
        if (count==k && -(double)score[i] > seeds[0].distance) continue;
        Hit hit={i,ids[i],-score[i]};
        insert(seeds,&count,k,hit,NULL);
    }
    count=0;*opened=0;
    for (int j=0;j<k;++j) {
        int64_t i=seeds[j].pos;
        double dist;
        if (!distance(&index,i,&dist)) goto done;
        Hit hit={i,ids[i],dist};insert(heap,&count,k,hit,&lost);
        score[i]=-INFINITY;
        ++*opened;
    }
    double cutoff=1-heap[0].distance/2;
    for (int64_t i=0;i<n;++i) {
        double s=score[i];
#ifdef HPIS_MAPPED_Q8
        if (compact) {
            /* Q8 is only a first screen. Compute the same signed FP32 score
             * before applying any of the original RAP bounds to a viable row. */
            if (s<cutoff-radius-coarse_error) continue;
            s=hpis_q8_fine_dot(plain+(size_t)i*d,projected,d);
        }
#endif
        if (s<cutoff-radius) continue;
        if (sealed) {
            /* Expand c itself before the nonlinear cap, whose derivative
             * becomes large near an antipodal query. */
            double c=fmax(-1,fmin(1,s*inverse_length[i]+slack));
            if (c<cap_cos[i] && c*cap_cos[i]+sqrt(fmax(0.,1-c*c))*cap_sin[i]<cutoff-slack) continue;
        }
        double dist;
        if (!distance(&index,i,&dist)) goto done;
        Hit hit={i,ids[i],dist};insert(heap,&count,k,hit,&lost);
        cutoff=1-heap[0].distance/2;
        ++*opened;
    }
    qsort(heap,k,sizeof(*heap),ordered);
    double tie_window=64.*(d+4)*DBL_EPSILON;
    opened[1]=(lost-heap[k-1].distance<=tie_window);
    for (int j=0;j<k;++j) {
        result[j]=heap[j].id;
        if (j && heap[j].distance-heap[j-1].distance<=tie_window) opened[1]=1;
    }
    status=0;
done:
    if (ctx) EVP_CIPHER_CTX_free(ctx);
    if (scratch) OPENSSL_cleanse(scratch,((size_t)d+4)*sizeof(*scratch));
    if (unit) OPENSSL_cleanse(unit,(size_t)d*sizeof(*unit));
    free(scratch);free(unit);free(heap);free(seeds);
    return status;
}

int hpis_rank(int64_t n, int d, int k, float *score, const int64_t *ids,
              const float *plain, const unsigned char *sealed,
              const unsigned char *key, const float *q,
              const double *inverse_length,const double *cap_sin,const double *cap_cos,
              double radius,double slack,int64_t *result,int64_t *opened) {
    return rank_impl(n,d,k,score,ids,plain,sealed,key,q,inverse_length,cap_sin,cap_cos,radius,slack,result,opened,NULL,NULL);
}

#ifdef HPIS_MAPPED_Q8
int hpis_rank_q8(int64_t n, int d, int k, float *score, const int64_t *ids,
              const float *plain, const unsigned char *sealed,
              const unsigned char *key, const float *q,
              const double *inverse_length,const double *cap_sin,const double *cap_cos,
              double radius,double slack,int64_t *result,int64_t *opened,void *compact,const float *projected) {
    return rank_impl(n,d,k,score,ids,plain,sealed,key,q,inverse_length,cap_sin,cap_cos,radius,slack,result,opened,compact,projected);
}
#endif
