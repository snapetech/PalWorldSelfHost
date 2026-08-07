#include "stdafx.h"
#include <stdio.h>
#include <stdlib.h>

int Kraken_Decompress(const byte *src, size_t src_len, byte *dst, size_t dst_len);

int main(int argc, char **argv) {
  if (argc != 4) {
    fprintf(stderr, "usage: %s <payload_in> <unpacked_size> <out>\n", argv[0]);
    return 2;
  }
  FILE *f = fopen(argv[1], "rb");
  if (!f) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
  fseek(f, 0, SEEK_END);
  long insz = ftell(f);
  fseek(f, 0, SEEK_SET);
  byte *inbuf = (byte*)malloc(insz);
  fread(inbuf, 1, insz, f);
  fclose(f);

  size_t unpacked = strtoull(argv[2], NULL, 10);
  byte *out = (byte*)malloc(unpacked + 65536);

  int outbytes = Kraken_Decompress(inbuf, insz, out, unpacked);
  fprintf(stderr, "input=%ld expected_unpacked=%zu got=%d\n", insz, unpacked, outbytes);
  if (outbytes != (int)unpacked) {
    fprintf(stderr, "MISMATCH\n");
    return 1;
  }
  FILE *o = fopen(argv[3], "wb");
  fwrite(out, 1, outbytes, o);
  fclose(o);
  fprintf(stderr, "OK\n");
  return 0;
}
