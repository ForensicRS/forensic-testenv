#!/usr/bin/env bash
# Generates a small NTFS volume with known content, using the real ntfs-3g tools (mkntfs + the
# ntfs-3g FUSE driver) in a throwaway Ubuntu container, plus the loose metadata files extracted
# with ntfscat, and a ground-truth file.
#
#   generators/ntfs_mkntfs.sh            # -> generators/out/ntfs_mkntfs/{vol.ntfs,$MFT,$Boot,$SDS,truth.tsv}
#
# Content (all under \Users\bob unless noted):
#   Documents\small.txt       resident, "hello ntfs"
#   Documents\big.bin         20000 bytes, non-resident, ADS Zone.Identifier (ZoneId=3)
#   hardlink.txt              second name of small.txt
#   comp\zeros.bin            262144 bytes of 'A' in an NTFS-compressed directory
#   sparse.dat                1 MiB sparse file, "SPARSE-DATA" at 512 KiB
#   gone.bin                  12000 bytes, then deleted (content should still be recoverable)
#   gone_small.txt            resident, "deleted but resident", then deleted
#
# ntfs-3g layouts differ from Windows ($SI v3 still used, no $UsnJrnl), so this complements, not
# replaces, a Windows-made sample.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out/ntfs_mkntfs"
rm -rf "$out"
mkdir -p "$out"

docker run --rm --device /dev/fuse --cap-add SYS_ADMIN --security-opt apparmor:unconfined \
  -v "$out:/out" ubuntu:24.04 bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y -qq ntfs-3g attr >/dev/null
    img=/out/vol.ntfs
    truncate -s 16M "$img"
    mkntfs -F -Q -q -L FRNSCTEST -c 4096 -s 512 "$img"
    mkdir -p /mnt/n
    ntfs-3g -o streams_interface=windows,compression "$img" /mnt/n
    d=/mnt/n/Users/bob
    mkdir -p "$d/Documents" "$d/comp"
    printf "hello ntfs" > "$d/Documents/small.txt"
    printf "0123456789abcdef%.0s" $(seq 1250) > "$d/Documents/big.bin"
    printf "[ZoneTransfer]\r\nZoneId=3\r\n" > "$d/Documents/big.bin:Zone.Identifier"
    ln "$d/Documents/small.txt" "$d/hardlink.txt"
    setfattr -n system.ntfs_attrib_be -v 0x00000810 "$d/comp"
    head -c 262144 /dev/zero | tr "\0" "A" > "$d/comp/zeros.bin"
    truncate -s 1M "$d/sparse.dat"
    printf "SPARSE-DATA" | dd of="$d/sparse.dat" bs=1 seek=524288 conv=notrunc status=none
    printf "deleted-content!%.0s" $(seq 750) > "$d/gone.bin"
    printf "deleted but resident" > "$d/gone_small.txt"
    sync
    rm "$d/gone.bin" "$d/gone_small.txt"
    sync
    umount /mnt/n
    cd /out
    ntfscat -i 0 "$img" > "\$MFT"
    ntfscat -i 7 "$img" > "\$Boot"
    ntfscat -i 9 -a 0x80 -n "\$SDS" "$img" > "\$SDS"
    chmod a+rw /out/*
  '

# Ground truth, computed from the same generator inputs (not from the image).
{
  printf 'path\tsize\tsha256\n'
  row() { printf '%s\t%s\t%s\n' "$1" "$(printf '%s' "$2" | wc -c)" "$(printf '%s' "$2" | sha256sum | cut -d" " -f1)"; }
  row 'Users/bob/Documents/small.txt' 'hello ntfs'
  printf 'Users/bob/Documents/big.bin\t20000\t%s\n' "$(printf '0123456789abcdef%.0s' $(seq 1250) | sha256sum | cut -d' ' -f1)"
  printf 'Users/bob/comp/zeros.bin\t262144\t%s\n' "$(head -c 262144 /dev/zero | tr '\0' A | sha256sum | cut -d' ' -f1)"
  printf 'deleted:Users/bob/gone.bin\t12000\t%s\n' "$(printf 'deleted-content!%.0s' $(seq 750) | sha256sum | cut -d' ' -f1)"
  row 'deleted:Users/bob/gone_small.txt' 'deleted but resident'
} > "$out/truth.tsv"
ls -la "$out"
