/**
 * A generated PNG, so a test can exercise the "technique does not apply" path
 * without committing a binary asset.
 *
 * Written with `node:zlib` rather than shelled out to the backend's Python: a test
 * that depends on a virtualenv existing one directory up is a test that breaks for
 * reasons unrelated to what it is checking. ~20 lines of stdlib beats that trade,
 * and the bytes are a real PNG that any decoder accepts.
 */

import { deflateSync } from "node:zlib";

/** CRC-32, as PNG chunks require. */
function crc32(buf: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of buf) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

/**
 * A grayscale PNG of `size` x `size`.
 *
 * Filled with a smooth gradient rather than a flat colour on purpose: ELA's floor
 * is a minimum pixel count, and a test that tripped over *that* instead of over the
 * JPEG-only rule would report the wrong reason for the wrong refusal.
 */
export function makePng(size = 128): Buffer {
  const raw = Buffer.alloc(size * (size + 1));
  for (let y = 0; y < size; y++) {
    raw[y * (size + 1)] = 0; // filter type 0 (None) for each scanline
    for (let x = 0; x < size; x++) {
      raw[y * (size + 1) + 1 + x] = Math.round((x / size) * 255);
    }
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 0; // colour type: grayscale
  ihdr[10] = 0; // deflate
  ihdr[11] = 0; // adaptive filtering
  ihdr[12] = 0; // no interlace

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
