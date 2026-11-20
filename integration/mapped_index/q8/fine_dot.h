#ifndef HPIS_MAPPED_Q8_FINE_DOT_H
#define HPIS_MAPPED_Q8_FINE_DOT_H

// Re-evaluate a surviving row using the original float32 matrix before
// applying the spherical-cap bound or opening its AEAD-sealed vector.
#if defined(__aarch64__)
#include <arm_neon.h>
#elif defined(__AVX2__)
#include <immintrin.h>
#endif
static inline float hpis_q8_fine_dot(const float *r, const float *q, int d) {
  int j = 0;
  float result = 0;
#if defined(__aarch64__)
  float32x4_t v0 = vdupq_n_f32(0), v1 = vdupq_n_f32(0), v2 = vdupq_n_f32(0),
              v3 = vdupq_n_f32(0);
  for (; j + 16 <= d; j += 16) {
    v0 = vfmaq_f32(v0, vld1q_f32(r + j), vld1q_f32(q + j));
    v1 = vfmaq_f32(v1, vld1q_f32(r + j + 4), vld1q_f32(q + j + 4));
    v2 = vfmaq_f32(v2, vld1q_f32(r + j + 8), vld1q_f32(q + j + 8));
    v3 = vfmaq_f32(v3, vld1q_f32(r + j + 12), vld1q_f32(q + j + 12));
  }
  result = vaddvq_f32(vaddq_f32(vaddq_f32(v0, v1), vaddq_f32(v2, v3)));
#elif defined(__AVX2__)
  __m256 v0 = _mm256_setzero_ps(), v1 = v0;
  for (; j + 16 <= d; j += 16) {
    v0 = _mm256_add_ps(
        v0, _mm256_mul_ps(_mm256_loadu_ps(r + j), _mm256_loadu_ps(q + j)));
    v1 = _mm256_add_ps(v1, _mm256_mul_ps(_mm256_loadu_ps(r + j + 8),
                                         _mm256_loadu_ps(q + j + 8)));
  }
  __m256 v = _mm256_add_ps(v0, v1);
  __m128 lo =
      _mm_add_ps(_mm256_castps256_ps128(v), _mm256_extractf128_ps(v, 1));
  lo = _mm_hadd_ps(lo, lo);
  lo = _mm_hadd_ps(lo, lo);
  result = _mm_cvtss_f32(lo);
#endif
  for (; j < d; j++)
    result += r[j] * q[j];
  return result;
}

#endif
