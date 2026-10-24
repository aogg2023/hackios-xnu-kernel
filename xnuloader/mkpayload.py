#!/usr/bin/env python3
"""
Build the "XNUP" payload consumed by the boot shim and the XNU loader.

  header : u32 magic "XNUP", u32 size (bytes after the header)
  entries: char name[16], u32 size, u32 reserved, data padded to 16 bytes
  end    : an entry whose name is empty

usage: mkpayload.py -o payload.bin name=file [name=file ...] [--cmdline "..."]
       [--require name=sha256 ...] [--require-cmd TOKEN ...] [--forbid-cmd TOKEN ...]

Input checks run before assembly: --require verifies the sha256 of the named
input file, --require-cmd demands an exact boot-arg token, --forbid-cmd
rejects a boot-arg prefix; any failure exits before writing the output.
The payload's sha256 is printed so the run record can bind it.
"""
import argparse, hashlib, struct, sys

ap = argparse.ArgumentParser()
ap.add_argument('-o', '--output', required=True)
ap.add_argument('--cmdline', default='')
ap.add_argument('--physbase', default='', help='hex: force XNU physical base (testing)')
ap.add_argument('--usb', action='store_true', help='make the loader stay resident and take commands over USB')
ap.add_argument('--rdres', default='', help='hex size of a root image already placed at the resident address (QEMU)')
ap.add_argument('--rdfile', default='', help='partition:size[:offset|tail] - staged system image on a GPT partition (phone)')
ap.add_argument('--checkpoint', default='', help='kernel checkpoint to stop at; 999 = loader self test')
ap.add_argument('--require', action='append', default=[], metavar='NAME=SHA256',
                help='refuse to build unless this input file hashes to this sha256')
ap.add_argument('--require-cmd', action='append', default=[], metavar='TOKEN',
                help='refuse to build unless the cmdline carries this exact token')
ap.add_argument('--forbid-cmd', action='append', default=[], metavar='PREFIX',
                help='refuse to build if the cmdline carries a token with this prefix')
ap.add_argument('items', nargs='*')
args = ap.parse_args()

inputs = {}
for item in args.items:
    name, path = item.split('=', 1)
    inputs[name] = path
for req in args.require:
    name, want = req.split('=', 1)
    if name not in inputs:
        sys.exit(f'require: no input named {name}')
    digest = hashlib.sha256(open(inputs[name], 'rb').read()).hexdigest()
    if digest != want:
        sys.exit(f'require: {name} ({inputs[name]}) is {digest}, wanted {want}')
tokens = args.cmdline.split()
for want in args.require_cmd:
    if want not in tokens:
        sys.exit(f'require-cmd: cmdline lacks {want}')
for bad in args.forbid_cmd:
    hit = [t for t in tokens if t == bad or t.startswith(bad + '=')]
    if hit:
        sys.exit(f'forbid-cmd: cmdline carries {hit[0]}')

body = b''
def add(name, data):
    global body
    body += struct.pack('<16sII', name.encode(), len(data), 0) + data + b'\0' * (-len(data) % 16)

for item in args.items:
    name, path = item.split('=', 1)
    add(name, open(path, 'rb').read())
if args.cmdline:
    add('cmdline', args.cmdline.encode() + b'\0')
if args.physbase:
    add('physbase', args.physbase.lower().replace('0x', '').encode() + b'\0')
if args.rdres:
    add('rdres', args.rdres.lower().replace('0x', '').encode() + b'\0')
if args.rdfile:
    add('rdfile', args.rdfile.encode() + b'\0')
if args.usb:
    add('usb', b'1\0')
if args.checkpoint:
    add('checkpoint', args.checkpoint.encode() + b'\0')
body += b'\0' * 24
payload = struct.pack('<II', 0x50554E58, len(body)) + body
open(args.output, 'wb').write(payload)
print(f'{args.output}: {len(payload)} bytes, sha256 {hashlib.sha256(payload).hexdigest()}')
