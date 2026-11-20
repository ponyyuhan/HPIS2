#ifndef HPIS_MAPPED_Q8_H
#define HPIS_MAPPED_Q8_H

#ifdef __cplusplus
extern "C" {
#endif

/* Prepare an immutable float32 sketch matrix or plaintext comparison matrix. */
void *coarse_prepare(const float *matrix, int rows, int dimension);
void coarse_free(void *prepared);

/* Fill rows float32 estimates and return a uniform upper bound on their
 * absolute dot-product error. A negative return indicates invalid input. */
double coarse_approx(void *prepared, const float *query, float *scores);

#ifdef __cplusplus
}
#endif
#endif
