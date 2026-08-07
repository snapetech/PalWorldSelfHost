# Palworld Kraken (Oodle) decompressor

Palworld's save format changed from plain zlib (`PlZ` magic) to Oodle
compression (`PlM` magic, Kraken variant) partway through 2026. No maintained
Python save-editing tool decompresses `PlM` saves; this narrow C++ build fills
that one gap so `scripts/save-currency-edit.py` can read current saves. It
only decompresses — Palworld still accepts writing plain zlib (`PlZ`) saves
back, so no Oodle *compressor* is needed or included.

## Provenance and licensing

The decompression algorithm (`kraken.cpp`, `bitknit.cpp`, `lzna.cpp`) is
**not vendored in this repository**. It comes from
[powzix/ooz](https://github.com/powzix/ooz), a widely used open
reimplementation of Oodle's Kraken/Mermaid/Selkie/Leviathan/LZNA/Bitknit
decompressors — but that upstream repository declares no license (no
`LICENSE` file, no license metadata). Rather than redistribute
unlicensed third-party source inside this repository, `fetch-build.sh`
downloads the three files directly from the pinned upstream commit and
verifies each against a reviewed SHA-256 before building, the same
download-and-verify posture `scripts/mod-lifecycle.py` uses for
PalDefender/UE4SS. Only `stdafx.h` (a portability shim) and
`palworld_decompress.cpp` (the minimal CLI wrapper around
`Kraken_Decompress`) are original to this project and committed here.

Pinned commit: `05038060aa68f9187ae9923b2388ca8db40e58d1` (2019-02-11).

## Building

```
./fetch-build.sh
```

Produces `./palworld_decompress`, a small native Linux binary (no Wine
required — the upstream Windows-only headers are shimmed out in
`stdafx.h`). Usage:

```
./palworld_decompress <compressed_payload_file> <expected_uncompressed_size> <output_file>
```

`<compressed_payload_file>` is the Palworld `.sav` file's bytes starting
right after its 12-byte outer header (4-byte uncompressed length, 4-byte
compressed length, 3-byte magic, 1-byte save type); `<expected_uncompressed_size>`
is that header's first field, read little-endian.
