// Q8 is a local acceleration of an existing index image. Its only input at
// setup is the matrix HPIC already has permission to score.
#include "q8.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <vector>

#if defined(__aarch64__) && defined(__ARM_FEATURE_DOTPROD)
#include <arm_neon.h>
#elif defined(__AVX2__)
#include <immintrin.h>
#endif

namespace {
constexpr int block_rows = 32;
constexpr int max_dimension = 4096;

struct QuantizedIndex {
  int rows;
  int dimension;
  int groups;
  std::vector<int8_t> weights;
  std::vector<float> scales;
  double max_residual_norm = 0;
  double max_approx_norm = 0;

  QuantizedIndex(const float *matrix, int n, int d)
      : rows(n), dimension(d), groups((d + 3) / 4),
        weights(((static_cast<size_t>(n) + block_rows - 1) / block_rows) *
                block_rows * groups * 4),
        scales(n) {
    for (int i = 0; i < rows; ++i) {
      const float *row = matrix + static_cast<size_t>(i) * dimension;
      double peak = 0;
      for (int j = 0; j < dimension; ++j) {
        if (!std::isfinite(row[j])) {
          throw std::invalid_argument("nonfinite vector index");
        }
        peak = std::max(peak, std::abs(static_cast<double>(row[j])));
      }
      const float scale = static_cast<float>(peak / 127.0);
      scales[i] = scale;
      double residual2 = 0, approx2 = 0;
      for (int j = 0; j < dimension; ++j) {
        int value = scale ? static_cast<int>(std::nearbyint(
                                static_cast<double>(row[j]) / scale))
                          : 0;
        value = std::clamp(value, -127, 127);
        const double estimate = static_cast<double>(value) * scale;
        const double residual = row[j] - estimate;
        residual2 += residual * residual;
        approx2 += estimate * estimate;
        const size_t offset =
            ((static_cast<size_t>(i / block_rows) * groups + j / 4) *
                 block_rows +
             i % block_rows) *
                4 +
            j % 4;
#if defined(__AVX2__)
        reinterpret_cast<uint8_t *>(weights.data())[offset] =
            static_cast<uint8_t>(value + 128);
#else
        weights[offset] = static_cast<int8_t>(value);
#endif
      }
      // The bound uses the largest outward-rounded value across all rows.
      const float residual_norm =
          std::nextafter(static_cast<float>(std::sqrt(residual2)), INFINITY);
      const float approx_norm =
          std::nextafter(static_cast<float>(std::sqrt(approx2)), INFINITY);
      max_residual_norm =
          std::max(max_residual_norm, static_cast<double>(residual_norm));
      max_approx_norm =
          std::max(max_approx_norm, static_cast<double>(approx_norm));
    }
  }
};

void score_blocks(const QuantizedIndex &index, const int8_t *query,
                  int query_sum, float query_scale, float *scores) {
  const int8_t *weights = index.weights.data();
  const size_t stride = static_cast<size_t>(index.groups) * block_rows * 4;
  for (int64_t first = 0; first < index.rows;
       first += block_rows, weights += stride) {
    int32_t sums[block_rows];
#if defined(__aarch64__) && defined(__ARM_FEATURE_DOTPROD)
    int32x4_t acc[block_rows / 4] = {};
    for (int group = 0; group < index.groups; ++group) {
      int32_t word;
      std::memcpy(&word, query + 4 * group, sizeof(word));
      const int8x16_t q = vreinterpretq_s8_s32(vdupq_n_s32(word));
      const int8_t *block = weights + group * block_rows * 4;
      for (int z = 0; z < block_rows / 4; ++z) {
        acc[z] = vdotq_s32(acc[z], vld1q_s8(block + 16 * z), q);
      }
    }
    for (int z = 0; z < block_rows / 4; ++z) {
      vst1q_s32(sums + 4 * z, acc[z]);
    }
#elif defined(__AVX2__)
    __m256i acc[block_rows / 8] = {};
    const __m256i ones = _mm256_set1_epi16(1);
    for (int group = 0; group < index.groups; ++group) {
      int32_t word;
      std::memcpy(&word, query + 4 * group, sizeof(word));
      const __m256i q = _mm256_set1_epi32(word);
      const int8_t *block = weights + group * block_rows * 4;
      for (int z = 0; z < block_rows / 8; ++z) {
        const __m256i w = _mm256_loadu_si256(
            reinterpret_cast<const __m256i *>(block + 32 * z));
        // VPMADDUBSW cannot saturate: two products have magnitude <= 2*255*63.
        const __m256i pairs = _mm256_maddubs_epi16(w, q);
        acc[z] = _mm256_add_epi32(acc[z], _mm256_madd_epi16(pairs, ones));
      }
    }
    const __m256i correction = _mm256_set1_epi32(128 * query_sum);
    for (int z = 0; z < block_rows / 8; ++z) {
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(sums + 8 * z),
                          _mm256_sub_epi32(acc[z], correction));
    }
#else
    std::fill(sums, sums + block_rows, 0);
    for (int group = 0; group < index.groups; ++group) {
      for (int z = 0; z < block_rows; ++z) {
        const int8_t *row = weights + (group * block_rows + z) * 4;
        for (int j = 0; j < 4; ++j) {
          sums[z] += static_cast<int>(row[j]) * query[group * 4 + j];
        }
      }
    }
#endif
    const int count =
        static_cast<int>(std::min<int64_t>(block_rows, index.rows - first));
    for (int z = 0; z < count; ++z) {
      scores[first + z] =
          static_cast<float>(sums[z]) * index.scales[first + z] * query_scale;
    }
  }
}
} // namespace

extern "C" void *coarse_prepare(const float *matrix, int rows, int dimension) {
  if (!matrix || rows < 1 || dimension < 1 || dimension > max_dimension) {
    return nullptr;
  }
  try {
    return new QuantizedIndex(matrix, rows, dimension);
  } catch (...) {
    return nullptr;
  }
}

extern "C" void coarse_free(void *prepared) {
  delete static_cast<QuantizedIndex *>(prepared);
}

extern "C" double coarse_approx(void *prepared, const float *query,
                                float *scores) {
  if (!prepared || !query || !scores) {
    return -1;
  }
  const auto &index = *static_cast<const QuantizedIndex *>(prepared);
  alignas(32) int8_t bytes[max_dimension] = {};
  double peak = 0;
  for (int j = 0; j < index.dimension; ++j) {
    if (!std::isfinite(query[j])) {
      return -1;
    }
    peak = std::max(peak, std::abs(static_cast<double>(query[j])));
  }
  if (peak == 0) {
    return -1;
  }
  const float scale = static_cast<float>(peak / 63.0);
  double residual2 = 0, norm2 = 0;
  int sum = 0;
  for (int j = 0; j < index.dimension; ++j) {
    int value = scale ? static_cast<int>(std::nearbyint(
                            static_cast<double>(query[j]) / scale))
                      : 0;
    value = std::clamp(value, -63, 63);
    bytes[j] = static_cast<int8_t>(value);
    sum += value;
    const double residual =
        static_cast<double>(query[j]) - static_cast<double>(value) * scale;
    residual2 += residual * residual;
    norm2 += static_cast<double>(query[j]) * query[j];
  }
  score_blocks(index, bytes, sum, scale, scores);
  const double query_norm = std::sqrt(norm2);
  const double rounding = (8 + index.dimension) *
                          std::numeric_limits<float>::epsilon() *
                          (1 + index.max_approx_norm) * (1 + query_norm);
  return index.max_residual_norm * query_norm +
         index.max_approx_norm * std::sqrt(residual2) + rounding;
}
