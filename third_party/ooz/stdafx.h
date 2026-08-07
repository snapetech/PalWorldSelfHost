#pragma once

#define _CRT_SECURE_NO_WARNINGS 1

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <sys/stat.h>
#include <stdint.h>
#include <emmintrin.h>

typedef unsigned char byte;
typedef unsigned char uint8;
typedef unsigned int uint32;
typedef uint64_t uint64;
typedef int64_t int64;
typedef signed int int32;
typedef unsigned short uint16;
typedef signed short int16;
typedef unsigned int uint;

static inline unsigned char _BitScanReverse(unsigned long *index, unsigned long mask) {
  if (!mask) return 0;
  *index = 31 - __builtin_clz(mask);
  return 1;
}
static inline unsigned char _BitScanForward(unsigned long *index, unsigned long mask) {
  if (!mask) return 0;
  *index = __builtin_ctz(mask);
  return 1;
}
static inline unsigned short _byteswap_ushort(unsigned short v) { return __builtin_bswap16(v); }
static inline unsigned long _byteswap_ulong(unsigned long v) { return __builtin_bswap32((uint32_t)v); }
static inline unsigned long long _byteswap_uint64(unsigned long long v) { return __builtin_bswap64(v); }

typedef struct { long long QuadPart; } LARGE_INTEGER;
static inline int QueryPerformanceCounter(LARGE_INTEGER *c) { c->QuadPart = 0; return 1; }
static inline int QueryPerformanceFrequency(LARGE_INTEGER *c) { c->QuadPart = 1; return 1; }

static inline unsigned long _rotl(unsigned long v, int shift) {
  return (v << shift) | (v >> (32 - shift));
}
#define __forceinline inline
